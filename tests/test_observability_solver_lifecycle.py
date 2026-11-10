"""Observabilité : retries/reculs/rallonges du solver rattachés et visibles.

Régression ciblée : ces events existaient dans events.jsonl mais n'étaient
jamais rendus. Le HTML montrait la tentative et son statut, pas la cause du
retry, la décision de rallonge ni les échecs d'alignement par étape.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_observability_report import (  # noqa: E402
    HTML_TEMPLATE,
    attach_llm_calls_by_mission,
    attach_solver_lifecycle_events,
)


def _episode():
    return {
        "mission_id": "m1",
        "session_id": "s1",
        "execution_tree": {
            "solver_id": "m1",
            "attempts": [
                {
                    "attempt_number": 1,
                    "nodes": [{"step_id": "step_1"}, {"step_id": "step_2"}],
                },
                {"attempt_number": 2, "nodes": []},
            ],
        },
    }


def test_attempt_lifecycle_events_attach_to_attempt():
    ep = _episode()
    events = [
        {"event": "attempt_started", "mission_id": "m1", "solver_id": "m1",
         "attempt_number": 1, "plan_id": "p1"},
        {"event": "attempt_failed", "mission_id": "m1", "solver_id": "m1",
         "attempt_number": 1, "failure_class": "plan_rejected_validation",
         "counts_toward_budget": False},
    ]

    attach_solver_lifecycle_events([ep], events)

    att = ep["execution_tree"]["attempts"][0]
    assert [e["event"] for e in att["_solver_events"]] == [
        "attempt_started",
        "attempt_failed",
    ]
    assert att["_solver_events"][1]["counts_toward_budget"] is False


def test_attempt_alias_key_is_supported():
    # `plan_rejected_validation` écrit `attempt`, pas `attempt_number`.
    ep = _episode()
    events = [{
        "event": "plan_warnings", "mission_id": "m1", "solver_id": "m1",
        "attempt": 2, "warnings": ["perception non consommée"],
    }]

    attach_solver_lifecycle_events([ep], events)

    att = ep["execution_tree"]["attempts"][1]
    assert att["_solver_events"][0]["event"] == "plan_warnings"


def test_step_scoped_events_go_to_the_node_not_the_attempt():
    ep = _episode()
    events = [{
        "event": "alignment_failure", "mission_id": "m1", "solver_id": "m1",
        "attempt_number": 1, "step_id": "step_2",
        "reason": "mauvais élément", "observed": "icône bonhour.txt",
    }]

    attach_solver_lifecycle_events([ep], events)

    att = ep["execution_tree"]["attempts"][0]
    node = att["nodes"][1]
    assert node["_step_events"][0]["event"] == "alignment_failure"
    assert not att.get("_solver_events")


def test_mission_level_events_kept_without_attempt():
    ep = _episode()
    events = [
        {"event": "solver_recul", "mission_id": "m1", "solver_id": "m1",
         "reason": "convergence_failure"},
        {"event": "retry_extension_decision", "mission_id": "m1",
         "solver_id": "m1", "approved": False, "reason": "pas de progrès"},
        {"event": "convergence.check", "mission_id": "m1", "verdict": "fail"},
    ]

    attach_solver_lifecycle_events([ep], events)

    assert {e["event"] for e in ep["_solver_events"]} == {
        "solver_recul", "retry_extension_decision", "convergence.check",
    }
    assert len(ep["_solver_events"]) == 3


def test_unattached_lifecycle_event_is_returned_not_dropped():
    ep = _episode()
    events = [{"event": "solver_recul", "mission_id": "ghost",
               "session_id": "other-session"}]

    unattached = attach_solver_lifecycle_events([ep], events)

    assert len(unattached) == 1
    assert unattached[0]["event"] == "solver_recul"


def test_retry_extension_llm_call_is_not_left_in_other_calls():
    ep = _episode()
    call = {
        "tag": "RetryExtensionDecision", "mission_id": "m1", "solver_id": "m1",
        "attempt_number": None, "response": {"approved": False},
    }

    attach_llm_calls_by_mission([ep], [call], [])

    assert ep["_retry_extension_calls"][0]["tag"] == "RetryExtensionDecision"
    assert not ep.get("_other_calls")


def test_html_template_exposes_the_new_surfaces():
    # Panneau central mission + bandeau par tentative + traces par étape.
    for marker in (
        "function renderCentralSolverLifecycle",
        "function renderLifecycleEvents",
        "function sortEventsByTs",
        "function inspectLooseCall",
        "html += renderCentralSolverLifecycle(ep);",
        "sortEventsByTs([...events, ...unattached])",
        "🔁 Cycle de retry",
        "Traces de cible & d'alignement",
    ):
        assert marker in HTML_TEMPLATE, marker


def test_lifecycle_summary_exposes_budget_and_failure_class():
    # Le résumé JS doit nommer la classe d'échec et le statut budget.
    assert "hors budget" in HTML_TEMPLATE
    assert "budget consommé" in HTML_TEMPLATE
    assert "failure_class" in HTML_TEMPLATE


def test_any_unconsumed_warning_is_attached_and_rendered():
    """La sortie 'any' non consommée doit être visible, pas seulement loguée."""
    ep = _episode()
    events = [{
        "event": "any_unconsumed_warning", "mission_id": "m1",
        "solver_id": "m1", "attempt_number": 2,
        "unconsumed": ["step_5 : sortie 'any' non lue"],
    }]

    attach_solver_lifecycle_events([ep], events)

    att = ep["execution_tree"]["attempts"][1]
    assert att["_solver_events"][0]["event"] == "any_unconsumed_warning"
    assert "any_unconsumed_warning" in HTML_TEMPLATE
    assert "case 'any_unconsumed_warning'" in HTML_TEMPLATE


def test_attached_events_are_sorted_chronologically():
    """Un JSONL désordonné ne doit pas produire un fil d'exécution inversé."""
    ep = _episode()
    events = [
        {"event": "attempt_failed", "mission_id": "m1", "solver_id": "m1",
         "attempt_number": 1, "ts": "2026-11-09T10:05:03+00:00",
         "failure_class": "convergence_failure"},
        {"event": "attempt_started", "mission_id": "m1", "solver_id": "m1",
         "attempt_number": 1, "ts": "2026-11-09T10:04:00+00:00"},
    ]

    attach_solver_lifecycle_events([ep], events)

    att = ep["execution_tree"]["attempts"][0]
    assert [e["event"] for e in att["_solver_events"]] == [
        "attempt_started",
        "attempt_failed",
    ]

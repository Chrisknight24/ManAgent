"""Filets observabilité : aucun llm_call ne doit être jeté silencieusement."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_observability_report import attach_llm_calls_by_mission


def _episode():
    return {
        "mission_id": "m1",
        "session_id": "s1",
        "execution_tree": {
            "solver_id": "m1",
            "attempts": [
                {
                    "attempt_number": 1,
                    "started_at": "2026-09-26T14:00:00+00:00",
                    "ended_at": "2026-09-26T15:00:00+00:00",
                    "nodes": [{"step_id": "step_1"}],
                }
            ],
        },
    }


def test_convergence_root_without_attempt_is_kept():
    ep = _episode()
    call = {"tag": "ConvergenceDecision", "mission_id": "m1",
            "solver_id": "m1", "step_id": None, "attempt_number": None}
    attach_llm_calls_by_mission([ep], [call], [])
    assert len(ep.get("_convergence_root_calls", [])) == 1
    tree = ep["execution_tree"]
    att = tree["attempts"][0]
    assert len(att.get("_convergence_calls", [])) >= 1


def test_presentator_without_solver_is_kept():
    ep = _episode()
    call = {"tag": "Presentator_output", "mission_id": "m1",
            "solver_id": None, "step_id": None, "attempt_number": None}
    attach_llm_calls_by_mission([ep], [call], [])
    assert len(ep.get("_presentator_calls", [])) == 1


def test_skill_synthesis_is_kept():
    ep = _episode()
    call = {"tag": "SkillSynthesis", "mission_id": "m1",
            "solver_id": "sub1", "step_id": "step_1", "attempt_number": None}
    attach_llm_calls_by_mission([ep], [call], [])
    assert len(ep.get("_skill_calls", [])) == 1


def test_llm_analyze_goes_to_node():
    ep = _episode()
    call = {"tag": "llm_analyze_data", "mission_id": "m1",
            "solver_id": "m1", "step_id": "step_1", "attempt_number": 1}
    attach_llm_calls_by_mission([ep], [call], [])
    node = ep["execution_tree"]["attempts"][0]["nodes"][0]
    assert len(node.get("_node_calls", [])) == 1

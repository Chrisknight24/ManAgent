"""Boucle Solver : le coupe-circuit F9 est réellement branché.

Les helpers purs sont testés dans `test_host_refusal_circuit.py`. Ici on
vérifie le comportement observable de `Solver.run()` : un refus hôte de
contrat cible autorise UNE correction, puis abandonne ; un échec ordinaire
garde le budget normal.
"""

import asyncio
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver
from core.plan_models import ExecutionStatus
from core.execution_models import FailureClass
from utils.logger import Logger


def _runtime():
    return SimpleNamespace(
        cancel_requested=False,
        solver_registry={},
        cache_manager=None,
        skill_registry=None,
        discovery_engine=None,
        execution_context=SimpleNamespace(scope=lambda **kw: nullcontext()),
        update_marker=lambda k, v: None,
        generation_epoch=0,
    )


def _solver(reasons):
    """Solver minimal : chaque exécution échoue avec le prochain `reasons[i]`."""
    s = Solver.__new__(Solver)
    s.id = "root-test"
    s.goal = "but générique"
    s.depth = 0
    s.context = ""
    s.parent = SimpleNamespace(id="parent")
    s.parent_step_id = None
    s.runtime_state = _runtime()
    s.signatures = []
    s._similar_missions = None
    s.variable_registry = {}
    s.current_attempt = None
    s._preexecution_failures = 0
    s.last_failure_bundle = None
    s.last_breakout_report = None
    s._candidate_skills = []

    calls = {"plans": 0, "executions": 0}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        calls["plans"] += 1
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
        idx = min(calls["executions"], len(reasons) - 1)
        calls["executions"] += 1
        return SimpleNamespace(
            status=ExecutionStatus.FAILED,
            final_context="",
            error_reason=reasons[idx],
            failure_class=FailureClass.EXECUTION_FAILURE,
            target_entity=None,
            failure_bundle=None,
            breakout_report=None,
            material_success_count=0,
            response=None,
            execution_tree=None,
        )

    async def fake_validate(*a, **k):
        return True

    async def fake_propagate(*a, **k):
        return None

    async def fake_skill(*a, **k):
        return None

    s._check_feasibility = fake_feasibility
    s.planner = SimpleNamespace(propose_plan=fake_plan, _cached_advice=None)
    s.executor = SimpleNamespace(execute_plan=fake_execute)
    s.validate_plan = fake_validate
    s.propagate_event = fake_propagate
    s._handle_skill_lifecycle_post_execution = fake_skill
    s._get_registry_metadata_view = lambda: {}
    s._format_registry_view = lambda meta: ""
    s.calls = calls
    return s


def _run_with_events(s, monkeypatch):
    events = []

    def fake_event(name, **kwargs):
        events.append((name, kwargs))

    monkeypatch.setattr(Logger, "event", staticmethod(fake_event))
    res = asyncio.run(s.run())
    return res, events


def test_two_host_refusals_abort_after_one_correction(monkeypatch):
    s = _solver([
        "TARGET_REQUIRED: target argument required",
        "TARGET_REQUIRED: target argument required",
        "TARGET_REQUIRED: target argument required",
    ])
    res, events = _run_with_events(s, monkeypatch)

    assert res.status == ExecutionStatus.FAILED
    # 1er refus -> une correction ; 2e refus -> abandon (pas de 3e cycle).
    assert s.calls["executions"] == 2
    assert s.calls["plans"] == 2
    assert any(name == "host_target_refusal" for name, _ in events)
    assert any(name == "host_target_refusal_circuit_open" for name, _ in events)


def test_ordinary_failures_keep_the_normal_budget(monkeypatch):
    s = _solver([
        "element introuvable A",
        "element introuvable B",
        "element introuvable C",
    ])
    res, events = _run_with_events(s, monkeypatch)

    assert res.status == ExecutionStatus.FAILED
    assert s.calls["executions"] == 3
    assert not any(name == "host_target_refusal_circuit_open" for name, _ in events)


def test_target_not_found_is_not_a_contract_refusal(monkeypatch):
    s = _solver([
        "TARGET_NOT_FOUND",
        "TARGET_NOT_FOUND",
        "TARGET_NOT_FOUND",
    ])
    res, events = _run_with_events(s, monkeypatch)

    assert res.status == ExecutionStatus.FAILED
    # Recréer la cible peut réparer : pas de coupe-circuit de contrat.
    assert not any(name == "host_target_refusal_circuit_open" for name, _ in events)

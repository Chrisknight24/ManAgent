"""Un refus avant exécution ne mange plus le budget (3 VRAIES exécutions)."""
import asyncio
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver  # noqa: F401 (import lourd mais partagé)
from core.plan_models import ExecutionStatus
from core.execution_models import FailureClass


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


def _solver(plan_behavior, verify_answers=None, verify_reasons=None):
    verify_answers = verify_answers or [True]
    verify_reasons = verify_reasons or ["but non atteint"]
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

    calls = {"plans": 0, "executions": 0, "verify": 0}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        calls["plans"] += 1
        action = plan_behavior[min(calls["plans"] - 1, len(plan_behavior) - 1)]
        if action == "reject":
            raise ValueError("plan invalide simulé")
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
        calls["executions"] += 1
        return SimpleNamespace(
            status=ExecutionStatus.SUCCESS,
            final_context="tour fini",
            error_reason=None,
            failure_class=FailureClass.NONE,
            target_entity=None,
            failure_bundle=None,
            breakout_report=None,
            material_success_count=0,
            response="ok",
            execution_tree=None,
        )

    async def fake_verify(final_result):
        calls["verify"] += 1
        n = min(calls["verify"] - 1, len(verify_answers) - 1)
        ok = verify_answers[n]
        why = verify_reasons[min(calls["verify"] - 1, len(verify_reasons) - 1)]
        return (ok, "" if ok else why)

    async def fake_validate(*a, **k):
        return True

    async def fake_propagate(*a, **k):
        return None

    async def fake_skill(*a, **k):
        return None

    s._check_feasibility = fake_feasibility
    s.planner = SimpleNamespace(propose_plan=fake_plan, _cached_advice=None)
    s.executor = SimpleNamespace(execute_plan=fake_execute)
    s._verify_mission_convergence = fake_verify
    s.validate_plan = fake_validate
    s.propagate_event = fake_propagate
    s._handle_skill_lifecycle_post_execution = fake_skill
    s._get_registry_metadata_view = lambda: {}
    s._format_registry_view = lambda meta: ""
    s.calls = calls
    return s


def test_two_rejections_then_success_costs_one_execution():
    s = _solver(["reject", "reject", "ok"])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.SUCCESS
    assert s.calls["plans"] == 3
    assert s.calls["executions"] == 1


def test_three_real_executions_then_give_up():
    s = _solver(["ok", "ok", "ok"], verify_answers=[False],
                verify_reasons=["ko A", "ko B", "ko C"])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.FAILED
    assert s.calls["executions"] == 3
    assert s.calls["plans"] == 3

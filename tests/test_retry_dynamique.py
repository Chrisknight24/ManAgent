"""Dynamisme : stérile abandonne, progrès continue, rallonge jugée une fois."""
import asyncio
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver  # noqa: F401 (import lourd mais partagé)
from core.plan_models import (
    ExecutionStatus,
    is_sterile_streak,
    normalize_failure_signature,
)
from core.execution_models import FailureClass


def test_signature_normalise_ids():
    a = normalize_failure_signature(FailureClass.EXECUTION_FAILURE, "Rejet étape step_1-00027 id abc123ef")
    b = normalize_failure_signature(FailureClass.EXECUTION_FAILURE, "Rejet étape step_9-00099 id def456ab")
    assert a == b


def test_sterile_detecte():
    assert is_sterile_streak([("s", 0), ("s", 0)]) is True
    assert is_sterile_streak([("s", 2), ("s", 1)]) is True
    assert is_sterile_streak([("a", 0), ("b", 0)]) is False
    assert is_sterile_streak([("s", 0), ("s", 3)]) is False
    assert is_sterile_streak([("s", 0)]) is False
    assert is_sterile_streak([]) is False


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


def _solver(error_reasons, materials=None, judge_answers=None):
    materials = materials or [0]
    judge_answers = judge_answers if judge_answers is not None else []
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

    calls = {"plans": 0, "executions": 0, "judge": 0}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        calls["plans"] += 1
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
        calls["executions"] += 1
        n = min(calls["executions"] - 1, len(error_reasons) - 1)
        m = materials[min(calls["executions"] - 1, len(materials) - 1)]
        return SimpleNamespace(
            status=ExecutionStatus.FAILED,
            final_context="raté",
            error_reason=error_reasons[n],
            failure_class=FailureClass.EXECUTION_FAILURE,
            target_entity="Executor",
            failure_bundle=None,
            breakout_report=None,
            material_success_count=m,
            response="",
            execution_tree=None,
        )

    async def fake_judge(solver_id, summary):
        calls["judge"] += 1
        assert "Budget standard" in summary
        return judge_answers[min(calls["judge"] - 1, len(judge_answers) - 1)]

    async def fake_verify(final_result):
        return (True, "")

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
    s.parent.request_retry_extension = fake_judge
    s.calls = calls
    return s


def test_sterile_resigns_after_two():
    s = _solver(["panne X step_1", "panne X step_2"], materials=[0, 0])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.FAILED
    assert s.calls["executions"] == 2
    assert "Abandon" in s.context
    assert s.calls["judge"] == 0


def test_different_failures_use_full_budget():
    s = _solver(["panne A", "panne B", "panne C"], materials=[0, 0, 0])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.FAILED
    assert s.calls["executions"] == 3
    assert s.calls["judge"] == 0


def test_extension_granted_once_then_capped():
    s = _solver(
        ["panne A", "panne B", "panne C", "panne D", "panne E"],
        materials=[2, 2, 2, 2, 2],
        judge_answers=[True, False],
    )
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.FAILED
    assert s.calls["executions"] == 4
    assert s.calls["judge"] == 2

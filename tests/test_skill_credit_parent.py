"""A4a : le crédit skill d'une sous-tâche revient au PARENT (après sa
ConvergenceDecision sur l'abstract_task), jamais au sous-solver lui-même."""
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


def _solver(depth, verify_answers):
    s = Solver.__new__(Solver)
    s.id = "test-solver"
    s.goal = "but générique"
    s.depth = depth
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

    calls = {"verify": 0, "skills": []}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
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

    async def fake_verify(final_result, strict=False):
        calls["verify"] += 1
        ok = verify_answers[min(calls["verify"] - 1, len(verify_answers) - 1)]
        return (ok, "" if ok else "but non atteint")

    async def fake_skill(is_success):
        calls["skills"].append(is_success)

    async def fake_validate(*a, **k):
        return True

    async def fake_propagate(*a, **k):
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


def test_root_solver_self_credits():
    s = _solver(depth=0, verify_answers=[True])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.SUCCESS
    assert s.calls["skills"] == [True]


def test_child_solver_does_not_self_credit():
    s = _solver(depth=1, verify_answers=[True])
    res = asyncio.run(s.run())
    assert res.status == ExecutionStatus.SUCCESS
    assert s.calls["skills"] == []


class _FakeChild:
    def __init__(self):
        self.calls = []

    async def _handle_skill_lifecycle_post_execution(self, is_success):
        self.calls.append(is_success)


def test_credit_abstract_child_delegates():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    child = _FakeChild()
    asyncio.run(ex._credit_abstract_child(child, True))
    asyncio.run(ex._credit_abstract_child(child, False))
    assert child.calls == [True, False]


def test_credit_abstract_child_none_is_noop():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    asyncio.run(ex._credit_abstract_child(None, True))

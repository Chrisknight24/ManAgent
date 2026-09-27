"""La convergence sémantique peut inspecter (PD), root comme sous-solvers."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.plan_models import ConvergenceDecision, PlanStep, StepType


def _step():
    return PlanStep(
        id="step_7", description="tâche générique", type=StepType.ABSTRACT_TASK,
        expected_result="travail fait",
    )


def _solver(discovery_enabled, engine=True):
    calls = {}

    async def fake_gen(prompt, schema, tag=None, mission_id=None, with_discovery=None, **kw):
        calls["with_discovery"] = with_discovery
        calls["tag"] = tag
        return ConvergenceDecision(is_convergent=True, reason="ok")

    llm = SimpleNamespace(
        _discovery_enabled=discovery_enabled,
        enable_discovery=lambda eng, ent: calls.update(enabled=True),
        disable_discovery=lambda: calls.update(disabled=True),
        set_data_context=lambda ctx: calls.update(context_set=True),
        generate_structured=fake_gen,
    )
    rs = SimpleNamespace(
        language="en",
        discovery_engine=SimpleNamespace() if engine else None,
        execution_context=SimpleNamespace(get=lambda k: "m1"),
    )
    solver = SimpleNamespace(
        llm=llm, runtime_state=rs, variable_registry={},
        parent=None, _data_providers={},
    )
    return solver, calls


def _executor(solver):
    ex = Executor.__new__(Executor)
    ex.solver = solver
    return ex


def test_root_uses_pd_without_touching_state():
    solver, calls = _solver(discovery_enabled=True)
    ex = _executor(solver)
    out = asyncio.run(ex._evaluate_semantic_convergence(_step(), "fini"))
    assert out.is_convergent is True
    assert calls["with_discovery"] is True
    assert "enabled" not in calls
    assert "disabled" not in calls


def test_sub_solver_gets_temporary_pd_then_restores():
    solver, calls = _solver(discovery_enabled=False)
    ex = _executor(solver)
    out = asyncio.run(ex._evaluate_semantic_convergence(_step(), "fini"))
    assert out.is_convergent is True
    assert calls["with_discovery"] is True
    assert calls.get("enabled") is True
    assert calls.get("disabled") is True


def test_no_engine_falls_back_gracefully():
    solver, calls = _solver(discovery_enabled=False, engine=False)
    ex = _executor(solver)
    out = asyncio.run(ex._evaluate_semantic_convergence(_step(), "fini"))
    assert out.is_convergent is True
    assert calls["with_discovery"] is True
    assert "enabled" not in calls

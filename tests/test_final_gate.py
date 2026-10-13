"""Fix 1+2 (trio faux-succès) : suspect final, strict sans monde, preuves étiquetées."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, is_suspect_final_answer
from core.solver import Solver


def _step(tool=True):
    if tool:
        return PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                        tool_name="keyboard", tool_args_json="{}",
                        expected_result="true")
    return PlanStep(id="step_9", description="r", type=StepType.DIRECT_ANSWER,
                    expected_result="ok", response_text="fini")


def test_suspect_seulement_sans_outil_apres_echecs():
    assert is_suspect_final_answer(_plan_tool(), [("x", 0)]) is False
    assert is_suspect_final_answer(_plan_notool(), []) is False
    assert is_suspect_final_answer(_plan_notool(), [("x", 0)]) is True


def _plan_tool():
    return Plan(goal="g", steps=[_step(True)])


def _plan_notool():
    return Plan(goal="g", steps=[_step(False)])


def _solver(calls, rum=None):
    async def _boom(*a, **k):
        calls["llm"] += 1
        raise AssertionError("LLM ne doit pas être appelé")

    class FakeTM:
        def known_tool_names(self):
            return set()

        def uncertain_tool_names(self):
            return set()

        def perception_tool_names(self):
            return set()

    solver = Solver.__new__(Solver)
    solver.id = "s1"
    solver.goal = "fermer sans enregistrer"
    solver.llm = types.SimpleNamespace(generate_structured=_boom)
    solver.current_attempt = types.SimpleNamespace(nodes=[])
    solver.runtime_state = types.SimpleNamespace(
        language="en", mission_rum=dict(rum or {}),
        tools_manager=FakeTM(),
        execution_context=types.SimpleNamespace(get=lambda k: "m1"),
    )
    return solver


def test_strict_sans_monde_refuse_sans_llm():
    calls = {"llm": 0}
    solver = _solver(calls)
    res = types.SimpleNamespace(response="tout est fermé")
    ok, why = asyncio.run(solver._verify_mission_convergence(res, strict=True))
    assert ok is False and calls["llm"] == 0
    assert "STRICTE" in why or "STRICT" in why or "monde" in why.lower()


def test_non_strict_sans_monde_passe_au_juge():
    calls = {"llm": 0}

    async def _ok(prompt, schema, tag=None, mission_id=None, **kw):
        calls["llm"] += 1
        calls["prompt"] = prompt
        from core.plan_models import ConvergenceDecision
        return ConvergenceDecision(is_convergent=True, reason="ok")

    solver = _solver(calls)
    solver.llm = types.SimpleNamespace(generate_structured=_ok)
    res = types.SimpleNamespace(response="tout est fermé")
    ok, _ = asyncio.run(solver._verify_mission_convergence(res, strict=False))
    assert ok is True and calls["llm"] == 1
    assert "[RÉPONSE]" in calls["prompt"]
    assert "UNKNOWN" in calls["prompt"]


def test_preuves_etiquetees_rum():
    calls = {"llm": 0}

    async def _ok(prompt, schema, tag=None, mission_id=None, **kw):
        calls["prompt"] = prompt
        from core.plan_models import ConvergenceDecision
        return ConvergenceDecision(is_convergent=True, reason="ok")

    solver = _solver(calls, rum={"data_x": {"value": "46", "attempt_no": 2}})
    solver.llm = types.SimpleNamespace(generate_structured=_ok)
    asyncio.run(solver._verify_mission_convergence(
        types.SimpleNamespace(response="46"), strict=False))
    assert "[RUM data_x t2]" in calls["prompt"]
    assert "46" in calls["prompt"]


def test_tag_attempt_marque_tentative():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(
        current_attempt=types.SimpleNamespace(attempt_number=3))
    entry = ex._tag_attempt({"value": "x"})
    assert entry["attempt_no"] == 3

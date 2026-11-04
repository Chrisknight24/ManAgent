"""Plan partiel sûr : accepté avec warning, vérifié par la convergence finale."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import PlanValidator
from core.solver import Solver


def _plan():
    return Plan(goal="g", steps=[
        PlanStep(id="step_1", description="attendre", type=StepType.TOOL_CALL,
                 tool_name="pause", tool_args_json="{}", expected_result="true"),
    ])


def _outcome(**overrides):
    values = {
        "is_conformant": True,
        "reason": "partiel sûr",
        "risk_level": "medium",
        "requires_human_confirmation": False,
        "irreversibility_flags": [],
        "is_partial": True,
        "partial_gaps": ["le fichier B n'est pas encore vérifié"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _validate(decision):
    async def fake_gen(prompt, schema, tag=None, **kw):
        return decision

    loader = SimpleNamespace(load=lambda *a, **k: "prompt")
    validator = PlanValidator(
        llm=SimpleNamespace(generate_structured=fake_gen),
        prompt_loader=loader, rules_text="", available_tools={"pause"},
    )
    return asyncio.run(validator.validate(_plan(), "solver-x", "g"))


def test_safe_partial_is_accepted_with_gaps():
    out = _validate(_outcome())
    assert bool(out) is True
    assert out.is_partial is True
    assert out.partial_gaps == ["le fichier B n'est pas encore vérifié"]


def test_critical_partial_is_rejected():
    out = _validate(_outcome(risk_level="critical"))
    assert bool(out) is False
    assert "partiel" in out.reason.lower()


def test_irreversible_partial_is_rejected():
    out = _validate(_outcome(irreversibility_flags=["step_1"]))
    assert bool(out) is False


def test_partial_with_human_confirmation_is_rejected():
    out = _validate(_outcome(requires_human_confirmation=True))
    assert bool(out) is False


def test_partial_without_gaps_falls_back_on_reason():
    out = _validate(_outcome(partial_gaps=[]))
    assert bool(out) is True
    assert out.partial_gaps == ["partiel sûr"]


def test_complete_plan_is_not_partial():
    out = _validate(_outcome(is_partial=False, partial_gaps=[]))
    assert bool(out) is True
    assert out.is_partial is False
    assert out.partial_gaps == []


def test_stale_gaps_are_ignored_when_plan_is_complete():
    out = _validate(_outcome(is_partial=False, partial_gaps=["vieux warning"]))
    assert bool(out) is True
    assert out.is_partial is False
    assert out.partial_gaps == []


def test_convergence_prompt_receives_plan_gaps():
    s = Solver.__new__(Solver)
    s.id = "test-solver"
    s.goal = "but"
    captured = {}

    async def fake_gen(prompt, schema, tag=None, **kw):
        captured["prompt"] = prompt
        return SimpleNamespace(is_convergent=True, reason="ok")

    s.llm = SimpleNamespace(generate_structured=fake_gen)
    s.runtime_state = SimpleNamespace(
        language="en",
        mission_rum={},
        perception_warnings_by_solver={},
        plan_warnings_by_solver={"test-solver": ["le fichier B n'est pas vérifié"]},
    )
    s.variable_registry = {}
    s.executor = SimpleNamespace(_perception_trail=[])
    s.current_attempt = SimpleNamespace(nodes=[])

    res = SimpleNamespace(response="ok", final_context="ok")
    asyncio.run(s._verify_mission_convergence(res))

    assert "le fichier B n'est pas vérifié" in captured["prompt"]

"""Le juge décide, pas les règles : un plan bla-bla atteint le juge."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import PlanValidator


def _blabla_plan():
    return Plan(goal="but générique", steps=[
        PlanStep(id="step_1", description="attendre", type=StepType.TOOL_CALL,
                 tool_name="pause", tool_args_json="{}",
                 expected_result="true"),
        PlanStep(id="step_2", description="conclure", type=StepType.DIRECT_ANSWER,
                 response_text="Mission accomplie.",
                 expected_result="any"),
    ])


def _validator(calls):
    async def fake_gen(prompt, schema, tag=None, **kw):
        calls["llm"] += 1
        return SimpleNamespace(
            is_conformant=True, reason="plan qui avance",
            risk_level="low", requires_human_confirmation=False,
            irreversibility_flags=[],
        )

    loader = SimpleNamespace(load=lambda *a, **k: "prompt")
    return PlanValidator(
        llm=SimpleNamespace(generate_structured=fake_gen),
        prompt_loader=loader, rules_text="",
        available_tools={"pause"},
    )


def test_blabla_atteint_le_juge():
    calls = {"llm": 0}
    v = _validator(calls)
    out = asyncio.run(v.validate(_blabla_plan(), "solver-x", "but générique"))
    assert calls["llm"] == 1
    assert bool(out) is True


def test_outil_inconnu_refuse_sans_llm():
    calls = {"llm": 0}
    v = _validator(calls)
    plan = Plan(goal="g", steps=[
        PlanStep(id="s1", description="d", type=StepType.TOOL_CALL,
                 tool_name="outil_fantome", tool_args_json="{}",
                 expected_result="true"),
    ])
    out = asyncio.run(v.validate(plan, "solver-x", "g"))
    assert bool(out) is False
    assert calls["llm"] == 0

"""Un direct_answer final doit prouver (donnée registre), pas affirmer."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_ungrounded_final_answer, PlanValidator


def _tool_step(sid="step_1"):
    return PlanStep(
        id=sid, description="agir", type=StepType.TOOL_CALL,
        tool_name="pause", tool_args_json="{}",
        expected_result="true",
    )


def _final_answer(text, execute_if=None):
    return PlanStep(
        id="step_9", description="conclure", type=StepType.DIRECT_ANSWER,
        response_text=text, execute_if=execute_if,
        expected_result="any",
    )


def test_blabla_final_flagged():
    plan = Plan(goal="but générique", steps=[
        _tool_step(),
        _final_answer("Mission accomplie, tout a été fait."),
    ])
    assert find_ungrounded_final_answer(plan) == ["step_9"]


def test_grounded_final_passes():
    plan = Plan(goal="but générique", steps=[
        _tool_step(),
        _final_answer("Résultat : $@_data_step_1"),
    ])
    assert find_ungrounded_final_answer(plan) == []


def test_conditional_branch_ignored():
    plan = Plan(goal="but générique", steps=[
        _tool_step(),
        _final_answer("Échec, rien fait.", execute_if="$@_bool_step_1 == False"),
    ])
    assert find_ungrounded_final_answer(plan) == []


def test_pure_chat_plan_ignored():
    plan = Plan(goal="but générique", steps=[
        _final_answer("Bonjour, voici la réponse."),
    ])
    assert find_ungrounded_final_answer(plan) == []


def test_validate_refuses_blabla_without_llm():
    v = PlanValidator(llm=None, prompt_loader=None, rules_text="",
                      available_tools={"pause"})
    plan = Plan(goal="but générique", steps=[
        _tool_step(),
        _final_answer("Mission accomplie."),
    ])
    out = asyncio.run(v.validate(plan, "solver-x", "but générique"))
    assert bool(out) is False
    assert "non prouvée" in out.reason

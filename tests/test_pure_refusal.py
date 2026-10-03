"""Refus honnete : plan 100% direct_answer = pas de validation. Non-regression M4 email."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.solver import _is_pure_direct_answer_plan


def _answer(sid="step_1"):
    return PlanStep(id=sid, description="pas d'outil", type=StepType.DIRECT_ANSWER,
                    expected_result="any", response_text="non")


def _tool(sid="step_1"):
    return PlanStep(id=sid, description="d", type=StepType.TOOL_CALL,
                    tool_name="perceive", tool_args_json="{}",
                    expected_result="true")


def test_pure_refusal_skips_validation():
    plan = Plan(goal="email impossible", steps=[_answer("step_1"), _answer("step_2")])
    assert _is_pure_direct_answer_plan(plan) is True


def test_mixed_plan_still_validated():
    plan = Plan(goal="g", steps=[_tool(), _answer("step_2")])
    assert _is_pure_direct_answer_plan(plan) is False


def test_tool_only_plan_still_validated():
    assert _is_pure_direct_answer_plan(Plan(goal="g", steps=[_tool()])) is False


def test_empty_plan_still_validated():
    assert _is_pure_direct_answer_plan(Plan(goal="g", steps=[])) is False


def test_solver_skips_validate_for_pure_refusal():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(base, "core", "solver.py"), encoding="utf-8").read()
    assert "_is_pure_direct_answer_plan(proposed_plan)" in text
    assert "plan_validation_skipped" in text

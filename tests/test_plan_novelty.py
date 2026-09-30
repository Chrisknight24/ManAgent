"""Nouveaute deterministe : le validateur voit les args. Non-regression du cas lwin->WIN."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.execution_models import PlanAttempt, FailureClass
from core.plan_validator import compare_plan_novelty, PlanValidator


def _tool_step(sid, tool, args, desc="d"):
    return PlanStep(
        id=sid, description=desc, type=StepType.TOOL_CALL,
        tool_name=tool, tool_args_json=json.dumps(args), expected_result="true",
    )


def _failed_attempt(plan, num=1, reason="boom"):
    return PlanAttempt(
        attempt_number=num, outcome="failed",
        proposed_plan=plan.model_dump(mode="json"),
        failure_class=FailureClass.EXECUTION_FAILURE,
        failure_reason=reason,
    )


def _press_plan(key):
    return Plan(goal="ouvrir", steps=[
        _tool_step("step_1", "press_key", {"key": key}, desc="touche Windows"),
        _tool_step("step_2", "type_text", {"text": "notepad"}, desc="taper"),
    ])


def test_no_history_means_none():
    assert compare_plan_novelty(_press_plan("WIN"), []) is None
    assert compare_plan_novelty(_press_plan("WIN"), None) is None


def test_identical_plan_flagged_identical():
    out = compare_plan_novelty(_press_plan("WIN"), [_failed_attempt(_press_plan("WIN"))])
    assert out is not None and "IDENTIQUE" in out


def test_fixed_arg_detected_as_modified():
    # Cas reel mission notepad : tentative 1 lwin (echec), tentative 2 WIN (correctif).
    out = compare_plan_novelty(_press_plan("WIN"), [_failed_attempt(_press_plan("lwin"))])
    assert out is not None
    assert "MODIFI" in out
    assert "key" in out and "lwin" in out and "WIN" in out


def test_new_structure_detected():
    other = Plan(goal="g", steps=[
        _tool_step("step_1", "click_target", {"x": 1}, desc="clic"),
    ])
    out = compare_plan_novelty(other, [_failed_attempt(_press_plan("lwin"))])
    assert out is not None and "STRUCTURE NOUVELLE" in out


def test_only_failed_attempts_compared():
    ok = PlanAttempt(attempt_number=1, outcome="success",
                     proposed_plan=_press_plan("WIN").model_dump(mode="json"))
    assert compare_plan_novelty(_press_plan("WIN"), [ok]) is None


def test_summary_shows_args():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _press_plan("WIN"))
    assert "WIN" in text and "notepad" in text

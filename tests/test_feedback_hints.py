"""Feedback qui soigne : options valides + vouliez-vous dire. Non-regression du cas screenshot."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planner import Planner
from core.plan_models import Plan, PlanStep, StepType


def _step(sid, **kw):
    base = dict(id=sid, description="d", type=StepType.TOOL_CALL,
                tool_name="perceive", tool_args_json="{}",
                expected_result="true")
    base.update(kw)
    return PlanStep(**base)


def test_unknown_var_lists_available_names():
    plan = Plan(goal="g", steps=[
        _step("step_1", output_variable_name="data_screenshot_var"),
        _step("step_2", tool_name="perceive", tool_args_json='{"source": "$@_data_step_1"}'),
    ])
    # step_2 utilise une variable inconnue : la piste doit lister les valides.
    plan2 = Plan(goal="g", steps=[
        _step("step_1", output_variable_name="data_screenshot_var"),
        _step("step_2", tool_args_json='{"source": "$@_data_screenshot_var_image"}'),
    ])
    ok, errors = Planner._validate_plan(None, plan2, {})
    assert ok is False
    blob = "\n".join(errors)
    assert "data_screenshot_var" in blob  # le bon nom est proposé


def test_close_name_gets_did_you_mean():
    plan = Plan(goal="g", steps=[
        _step("step_1", output_variable_name="data_screenshot_var"),
        _step("step_2", tool_args_json='{"source": "$@_data_screenshot_var_image"}'),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False
    blob = "\n".join(errors)
    assert "Vouliez-vous dire" in blob or "voulez-vous" in blob.lower()


def test_far_name_gets_no_guess_but_options():
    plan = Plan(goal="g", steps=[
        _step("step_1", output_variable_name="data_x"),
        _step("step_2", tool_args_json='{"source": "$@_data_zzzqqq"}'),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False
    blob = "\n".join(errors)
    assert "disponibles" in blob.lower()  # la liste est là
    assert "Vouliez-vous dire" not in blob  # mais pas de devinette lointaine


def test_valid_plan_unchanged():
    plan = Plan(goal="g", steps=[
        _step("s1", output_variable_name="bool_x"),
        _step("s2", execute_if="$@_bool_x == True"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is True, errors

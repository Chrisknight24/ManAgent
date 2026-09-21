"""Tests gate déterministe : outils/skills inconnus refusés sans LLM."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, is_mission_success
from core.plan_validator import find_unknown_plan_tools


def _plan(*steps):
    return Plan(goal="test", steps=list(steps))


def _tool_step(tool_name, args="{}"):
    return PlanStep(
        id="s1", description="d", type=StepType.TOOL_CALL,
        tool_name=tool_name, tool_args_json=args,
        expected_result="true",
    )


def test_known_tool_passes():
    plan = _plan(_tool_step("extract_json_value"))
    assert find_unknown_plan_tools(plan, {"extract_json_value"}, set()) == []


def test_unknown_tool_flagged():
    plan = _plan(_tool_step("click"))
    assert find_unknown_plan_tools(plan, {"extract_json_value"}, set()) == ["tool:click"]


def test_execute_skill_needs_production():
    plan = _plan(_tool_step("execute_skill", '{"skill_id": "x.y"}'))
    assert find_unknown_plan_tools(plan, {"execute_skill"}, {"x.y"}) == []
    assert find_unknown_plan_tools(plan, {"execute_skill"}, set()) == ["skill:x.y"]
    assert find_unknown_plan_tools(plan, {"execute_skill"}, {"other"}) == ["skill:x.y"]


def test_no_referential_means_no_check():
    plan = _plan(_tool_step("anything"))
    assert find_unknown_plan_tools(plan, None, None) == []


def test_non_tool_steps_ignored():
    plan = _plan(PlanStep(id="s1", description="d", type=StepType.DIRECT_ANSWER,
                          expected_result="any"))
    assert find_unknown_plan_tools(plan, set(), set()) == []


def test_mission_success_needs_material_action():
    assert is_mission_success(False, 0) == (True, "")
    assert is_mission_success(True, 2)[0] is True
    ok, why = is_mission_success(True, 0)
    assert ok is False and "matérielle" in why

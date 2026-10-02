"""Lot 1 : faits moteur pour le juge + oui/non insensible + rules nettoye."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.execution_models import PlanAttempt, FailureClass
from core.plan_validator import PlanValidator


def _tool_step(sid, tool, args="{}", out=None, exp="true"):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name=tool, tool_args_json=args, expected_result=exp,
        output_variable_name=out,
    )


def _attempt(num, plan, outcome="failed", nodes=None):
    return PlanAttempt(
        attempt_number=num, outcome=outcome,
        proposed_plan=plan.model_dump(mode="json"),
        failure_class=FailureClass.EXECUTION_FAILURE if outcome == "failed" else FailureClass.NONE,
        failure_reason="boom" if outcome == "failed" else "",
        nodes=nodes or [],
    )


def _plan():
    return Plan(goal="g", steps=[
        _tool_step("step_1", "perceive", "{}", out="data_vue"),
        _tool_step("step_2", "press_key", '{"key": "WIN"}'),
    ])


def test_summary_shows_output_var_and_expected():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _plan())
    assert "data_vue" in text
    assert "attend=true" in text


def test_repetition_fact_types_preexec_vs_executed():
    plan = _plan()
    none = PlanValidator.build_repetition_fact(plan, [])
    assert none is None
    other = Plan(goal="g", steps=[_tool_step("step_1", "click_target", "{}")])
    assert PlanValidator.build_repetition_fact(other, [_attempt(1, plan)]) is None
    fact = PlanValidator.build_repetition_fact(plan, [_attempt(1, plan)])
    assert "AVANT" in fact  # pas de nodes = jamais exécuté


def test_repetition_fact_executed_counted():
    from core.execution_models import ExecutionNode
    plan = _plan()
    node = ExecutionNode(step_id="step_1", description="d", step_type="tool_call",
                         status="failed")
    fact = PlanValidator.build_repetition_fact(plan, [_attempt(1, plan, nodes=[node])])
    assert "EXÉCUTION" in fact


def test_direct_perception_note_lists_steps():
    plan = Plan(goal="g", steps=[
        _tool_step("step_1", "perceive", "{}"),
    ])
    note = PlanValidator.build_direct_perception_note(plan, {"perceive"}, {})
    assert note is not None and "step_1" in note
    assert PlanValidator.build_direct_perception_note(plan, set(), {}) is None


def _executor():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(variable_registry={})
    return ex


def test_yes_no_answers_match_case_insensitive():
    ex = _executor()
    assert ex._evaluate_condition('"Yes" == "yes"') is True
    assert ex._evaluate_condition('"OUI." == "oui"') is True
    assert ex._evaluate_condition('"no" != "yes"') is True
    assert ex._evaluate_condition('"yes" == "no"') is False


def test_exact_content_still_matters():
    ex = _executor()
    assert ex._evaluate_condition('"cmd" == "cmd"') is True
    assert ex._evaluate_condition('"cmd" == "notepad"') is False


def test_rules_points_at_judge_and_screenshots():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(base, "rules.md"), encoding="utf-8").read()
    assert "JUGE" in text
    assert "capture d'écran" in text or "capture d" in text

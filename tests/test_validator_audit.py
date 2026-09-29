"""Audit validateur : rejets code visibles, direct_answer libre, prose souple."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.planner import Planner
from core.plan_validator import find_unknown_plan_tools, find_malformed_step_args
from tools.build_observability_report import attach_code_validation_events


def _tool_step(sid="s1", tool="perceive", args="{}"):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name=tool, tool_args_json=args, expected_result="true",
    )


def _answer_step(sid="s1", text="Refus honnête : aucun outil.", execute_if=None):
    return PlanStep(
        id=sid, description="réponse", type=StepType.DIRECT_ANSWER,
        expected_result="any", response_text=text, execute_if=execute_if,
    )


def test_code_rejection_attached_to_attempt():
    ep = {
        "mission_id": "m1",
        "execution_tree": {
            "solver_id": "m1",
            "attempts": [{"attempt_number": 1, "nodes": []}],
        },
    }
    ev = {
        "event": "plan_validation_decision", "mission_id": "m1",
        "solver_id": "m1", "attempt_number": 1,
        "is_valid": False, "reason": "outil inconnu: tool:click",
    }
    attach_code_validation_events([ep], [ev])
    att = ep["execution_tree"]["attempts"][0]
    assert len(att.get("_code_validations", [])) == 1
    assert len(ep.get("_code_validations", [])) == 1


def test_rejected_supervisor_with_attempt_field_attached():
    ep = {
        "mission_id": "m1",
        "execution_tree": {
            "solver_id": "m1",
            "attempts": [{"attempt_number": 2, "nodes": []}],
        },
    }
    ev = {
        "event": "plan_rejected_supervisor", "mission_id": "m1",
        "solver_id": "m1", "attempt": 2, "reason": "juge LLM : non conforme",
    }
    attach_code_validation_events([ep], [ev])
    att = ep["execution_tree"]["attempts"][0]
    assert len(att.get("_code_validations", [])) == 1


def test_direct_answer_honest_without_var_accepted():
    plan = Plan(goal="g", steps=[_answer_step()])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is True, errors
    # Aucune gate code sur le texte libre.
    assert find_unknown_plan_tools(plan, set(), set()) == []
    assert find_malformed_step_args(plan) == []


def test_direct_answer_branches_with_execute_if_accepted():
    plan = Plan(goal="g", steps=[
        _tool_step(sid="step_1", tool="perceive"),
        _answer_step(sid="step_2", text="OK", execute_if="$@_bool_step_1 == True"),
        _answer_step(sid="step_3", text="KO", execute_if="$@_bool_step_1 == False"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is True, errors


def test_prose_unknown_var_is_warning_only():
    plan = Plan(goal="g", steps=[
        PlanStep(
            id="s1", description="Voir $@_data_fantome comme exemple.",
            type=StepType.TOOL_CALL, tool_name="perceive",
            tool_args_json="{}", expected_result="true",
        ),
    ])
    ok, warnings = Planner._validate_plan(None, plan, {})
    assert ok is True
    assert len(warnings) >= 1


def test_executable_unknown_var_still_rejected():
    plan = Plan(goal="g", steps=[
        _tool_step(sid="s1", args='{"x": "$@_data_missing"}'),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False and errors

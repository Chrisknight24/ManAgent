"""Tests validation perso : prose auto-référence OK, exécutable strict."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planner import Planner
from core.plan_models import Plan, PlanStep, StepType


def _step(sid, **kw):
    base = dict(id=sid, description="d", type=StepType.TOOL_CALL,
                tool_name="perceive", tool_args_json="{}",
                expected_result="true")
    base.update(kw)
    return PlanStep(**base)


def test_prose_self_reference_allowed():
    plan = Plan(goal="g", steps=[
        _step("s1", output_variable_name="bool_x",
              step_context="Produit $@_bool_x pour la suite."),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is True, errors


def test_execute_if_unknown_still_rejected():
    plan = Plan(goal="g", steps=[
        _step("s1", execute_if="$@_bool_missing == True"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False and errors


def test_execute_if_prior_output_allowed():
    plan = Plan(goal="g", steps=[
        _step("s1", output_variable_name="bool_x"),
        _step("s2", execute_if="$@_bool_x == True"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is True, errors

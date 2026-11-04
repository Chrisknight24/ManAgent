"""Résumé de plan pour le juge : la chaîne de cible doit être visible.

Le juge voyait `args={"target": "$@_rect_tool"}` mais jamais le step qui
produit cet alias (`output_target_name`), donc il pouvait conclure à une
référence inventée et refuser un plan correct.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import PlanValidator


def test_producer_alias_is_rendered_in_the_plan_summary():
    plan = Plan(goal="g", steps=[
        PlanStep(
            id="step_1",
            description="creer la cible rectangle",
            type=StepType.TOOL_CALL,
            tool_name="target",
            tool_args_json='{"cell": "E2", "label": "outil rectangle"}',
            output_target_name="rect_tool",
            expected_result="true",
        ),
        PlanStep(
            id="step_2",
            description="cliquer",
            type=StepType.TOOL_CALL,
            tool_name="mouse",
            tool_args_json='{"action": "click", "target": "$@_rect_tool"}',
            expected_result="true",
        ),
    ])
    summary = PlanValidator._summarize_plan_for_prompt(None, plan)
    assert "target=rect_tool" in summary
    assert "$@_rect_tool" in summary


def test_no_alias_no_noise():
    plan = Plan(goal="g", steps=[
        PlanStep(
            id="step_1",
            description="ouvrir Paint",
            type=StepType.TOOL_CALL,
            tool_name="launch",
            tool_args_json='{"app": "paint"}',
            expected_result="true",
        ),
    ])
    summary = PlanValidator._summarize_plan_for_prompt(None, plan)
    assert "target=" not in summary

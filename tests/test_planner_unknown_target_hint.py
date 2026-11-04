"""Validation legacy du planner : orienter une ref de cible vers `output_target_name`.

Bug réel (mission `b39a11ae`, 28/10 21:42:56) : le plan 3 utilise
`$@_canvas_start` / `$@_canvas_end` comme `target` de `mouse`, mais les
étapes `target` n'ont pas déclaré `output_target_name`. Le rejet déterministe
est correct, MAIS le message legacy (`core/planner.py`) ne mentionne que
`output_variable_name` — il court-circuite `find_unresolved_plan_refs` (F6)
et n'oriente pas le planner vers le bon champ.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.planner import Planner


def _target_step():
    return PlanStep(
        id="step_1",
        description="créer la cible de l'outil rectangle",
        type=StepType.TOOL_CALL,
        tool_name="target",
        tool_args_json='{"cell": "F2", "label": "outil rectangle"}',
        expected_result="true",
    )


def _mouse_step():
    return PlanStep(
        id="step_2",
        description="cliquer sur l'outil rectangle",
        type=StepType.TOOL_CALL,
        tool_name="mouse",
        tool_args_json='{"action": "click", "target": "$@_rect_tool"}',
        expected_result="true",
    )


def test_unknown_target_ref_points_to_output_target_name():
    ok, errors = Planner._validate_plan(
        None, Plan(goal="g", steps=[_target_step(), _mouse_step()]), {}
    )

    assert not ok
    joined = "\n".join(errors)
    assert "rect_tool" in joined
    assert "output_target_name" in joined


def test_unknown_non_target_ref_keeps_the_historical_message():
    step = PlanStep(
        id="step_1",
        description="écrire un fichier",
        type=StepType.TOOL_CALL,
        tool_name="write_file",
        tool_args_json='{"path": "$@_unknown_path"}',
        expected_result="true",
    )

    ok, errors = Planner._validate_plan(None, Plan(goal="g", steps=[step]), {})

    assert not ok
    joined = "\n".join(errors)
    assert "unknown_path" in joined
    assert "output_target_name" not in joined

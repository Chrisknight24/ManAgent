"""F4 : le résumé de plan du Solver doit montrer la cible produite.

Sans `target=`, le modèle relit son plan rejeté sans voir la chaîne
producteur → consommateur, et ne peut pas comprendre pourquoi
`$@_rect_tool` a été refusé.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from core.plan_models import Plan, PlanStep, StepType
from core.solver import Solver


def _plan(with_target):
    return Plan(goal="g", steps=[
        PlanStep(
            id="step_1",
            description="creer la cible",
            type=StepType.TOOL_CALL,
            tool_name="target",
            tool_args_json='{"cell": "E2", "label": "outil rectangle"}',
            output_target_name="rect_tool" if with_target else None,
            expected_result="true",
        ),
    ])


def test_solver_summary_shows_the_target_alias():
    summary = Solver._summarize_plan(SimpleNamespace(), _plan(True))
    assert "target=rect_tool" in summary


def test_solver_summary_has_no_target_noise():
    summary = Solver._summarize_plan(SimpleNamespace(), _plan(False))
    assert "target=" not in summary

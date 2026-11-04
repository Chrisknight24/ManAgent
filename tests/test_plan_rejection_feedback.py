"""F1 : le feedback de retry doit montrer le plan réellement rejeté.

Bug réel (missions YouTube 2026-10-31) : `propose_plan` lève `ValueError`
après avoir posé `planner._last_proposed_plan`. La variable locale
`proposed_plan` du Solver reste donc `None`, et le prompt de retry affiche
« Aucun plan généré (erreur de validation) » — le modèle ne voit jamais ce
qu'il a écrit, donc il répète la même faute.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from core.plan_models import Plan, PlanStep, StepType
from core.solver import Solver


def _plan():
    return Plan(goal="g", steps=[
        PlanStep(
            id="step_1",
            description="ouvrir Paint",
            type=StepType.TOOL_CALL,
            tool_name="launch",
            tool_args_json='{"app": "paint"}',
            expected_result="true",
        ),
    ])


def _stub(last_plan):
    stub = SimpleNamespace(planner=SimpleNamespace(_last_proposed_plan=last_plan))
    return stub


def test_rejected_plan_falls_back_to_the_planner_last_plan():
    plan = _plan()
    resolved = Solver._resolve_rejected_plan(_stub(plan), None)
    assert resolved is plan


def test_explicit_plan_wins_over_the_fallback():
    explicit = _plan()
    other = _plan()
    other.goal = "autre"
    resolved = Solver._resolve_rejected_plan(_stub(other), explicit)
    assert resolved is explicit


def test_no_plan_anywhere_stays_none():
    assert Solver._resolve_rejected_plan(_stub(None), None) is None
    assert Solver._resolve_rejected_plan(SimpleNamespace(), None) is None

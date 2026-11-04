"""F6 : une seule construction du feedback de retry.

Le bloc `[⚠️ REJET DU PLAN PRÉCÉDENT]` était dupliqué à l'identique dans les
deux branches d'exception de `Solver.run` (`ValidationError` et `ValueError`).
Toute correction future doit passer par un seul endroit.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from core.solver import Solver


def test_feedback_contains_error_summary_and_instruction():
    feedback = Solver._build_plan_retry_feedback(
        SimpleNamespace(),
        "Plan invalide :\nRéférences inconnues : $@_target_step_3",
        "Structure du plan rejeté :\n  - step_1: type=tool_call",
    )

    assert "REJET DU PLAN PRÉCÉDENT" in feedback
    assert "$@_target_step_3" in feedback
    assert "step_1: type=tool_call" in feedback
    assert "Veuillez corriger cette erreur" in feedback


def test_feedback_is_deterministic():
    stub = SimpleNamespace()
    first = Solver._build_plan_retry_feedback(stub, "err", "sum")
    second = Solver._build_plan_retry_feedback(stub, "err", "sum")
    assert first == second

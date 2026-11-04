"""Event `plan_validation_decision` : le plan partiel doit être visible.

Le front et l'observabilité ne voyaient que `is_valid` : un plan accepté
avec lacunes était indiscernable d'un plan complet, alors que la
convergence finale en dépend.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import build_plan_validation_payload


def _outcome(**overrides):
    values = dict(
        is_valid=True,
        reason="ok",
        risk_level=SimpleNamespace(value="low"),
        requires_human_confirmation=False,
        human_confirmed=False,
        irreversibility_flags=[],
        is_partial=True,
        partial_gaps=["le carre n'est pas dessine"],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_partial_fields_are_exposed():
    payload = build_plan_validation_payload(_outcome(), "s1", "p1")
    assert payload["solver_id"] == "s1"
    assert payload["plan_id"] == "p1"
    assert payload["is_partial"] is True
    assert payload["partial_gaps"] == ["le carre n'est pas dessine"]
    assert payload["risk_level"] == "low"


def test_complete_plan_has_no_partial_flag():
    payload = build_plan_validation_payload(
        _outcome(is_partial=False, partial_gaps=[]), "s1", "p1"
    )
    assert payload["is_partial"] is False
    assert payload["partial_gaps"] == []


def test_gaps_are_capped():
    payload = build_plan_validation_payload(
        _outcome(partial_gaps=[f"lacune {i}" for i in range(20)]), "s1", "p1"
    )
    assert len(payload["partial_gaps"]) == 8

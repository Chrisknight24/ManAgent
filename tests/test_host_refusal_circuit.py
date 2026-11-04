"""Refus hôte déterministe : un seul nouvel essai, jamais de cycle complet.

Bug reel (mission `008bcdba`) : `TARGET_REQUIRED` renvoye par l'hote apres
dispatch a relance faisabilite + planner + validation, alors qu'aucune
reformulation de strategie ne peut reparer un contrat d'argument. Un refus
de contrat hote = un essai de correction, puis abandon explicite.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import (
    host_refusal_budget_exhausted,
    is_host_target_contract_refusal,
)


def test_target_required_is_a_host_contract_refusal():
    assert is_host_target_contract_refusal("TARGET_REQUIRED") is True


def test_target_schema_invalid_is_a_host_contract_refusal():
    assert is_host_target_contract_refusal(
        "TARGET_SCHEMA_INVALID: consumer 'target' expects a local target name"
    ) is True


def test_other_failures_are_not_host_contract_refusals():
    for reason in (
        "TARGET_NOT_FOUND",
        "element introuvable",
        "no_world_change_detected",
        "",
        None,
    ):
        assert is_host_target_contract_refusal(reason) is False


def test_one_correction_attempt_then_abort():
    # 1er refus -> un nouvel essai autorise ; 2e refus -> abandon.
    assert host_refusal_budget_exhausted(1) is False
    assert host_refusal_budget_exhausted(2) is True
    assert host_refusal_budget_exhausted(3) is True

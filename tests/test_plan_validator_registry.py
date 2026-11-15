"""Validateur : un nom de cible deja publie n'est pas une devinette.

Vecu (run dcc9ec4b) : le planner a produit `$@_desktop_space`, nom affiche
dans le bloc REGISTRY du prompt. Le validateur l'a declare « inconnu » deux
fois de suite, brulant deux tentatives pour un plan correct.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import PlanValidator, find_unresolved_plan_refs


def _consumer(target="$@_desktop_space"):
    return PlanStep(
        id="step_1",
        description="ouvrir le menu contextuel du bureau",
        type=StepType.TOOL_CALL,
        tool_name="click",
        tool_args_json='{"action": "right_click", "target": "%s"}' % target,
        expected_result="true",
    )


def _plan(target="$@_desktop_space"):
    return Plan(goal="trier le bureau", steps=[_consumer(target)])


# --------------------------------------------------------------------------
# Coeur du correctif
# --------------------------------------------------------------------------

def test_nom_du_registre_accepte():
    hits = find_unresolved_plan_refs(_plan(), extra_names=["desktop_space"])
    assert hits == []


def test_nom_du_registre_accepte_avec_prefixe():
    hits = find_unresolved_plan_refs(_plan(), extra_names=["$@_desktop_space"])
    assert hits == []


def test_nom_inconnu_partout_reste_refuse():
    hits = find_unresolved_plan_refs(_plan("$@_cible_inventee"), extra_names=["desktop_space"])
    assert len(hits) == 1
    assert "cible_inventee" in hits[0]


def test_sans_extra_names_le_comportement_est_inchange():
    hits = find_unresolved_plan_refs(_plan())
    assert len(hits) == 1


def test_callable_resolu_paresseusement():
    called = {"n": 0}

    def _names():
        called["n"] += 1
        return ["desktop_space"]

    assert find_unresolved_plan_refs(_plan(), extra_names=_names) == []
    assert called["n"] == 1


def test_callable_qui_leve_ne_casse_pas_le_gate():
    def _boom():
        raise RuntimeError("registre indisponible")

    hits = find_unresolved_plan_refs(_plan(), extra_names=_boom)
    # Le gate reste honnete : sans information, un nom inconnu reste refuse.
    assert len(hits) == 1


# --------------------------------------------------------------------------
# Branchement : le validateur consulte le registre runtime
# --------------------------------------------------------------------------

def test_validator_branche_known_target_names():
    validator = PlanValidator(
        llm=None,
        prompt_loader=None,
        rules_text="",
        available_tools={"click"},
        tools_manager=None,
        known_target_names=lambda: ["desktop_space"],
    )
    assert validator._known_target_names() == ["desktop_space"]

"""Prompt de convergence : il juge la MISSION, pas la derniere etape.

Bug reel (mission `ee848a24`) : le prompt recevait
`step_description = "Appuyer sur Entree"` et `expected_result = <but complet>`,
puis la regle 4 autorisait « si l'essentiel est atteint, accepte » — le modele
moyen s'est ancre sur l'etape et a ignore la regle 9 (lacunes). Les deux
regles se contredisaient ; la porte deterministe (`gap_convergence_refusal`)
est le filet, ce prompt est la premiere ligne.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader

_GAPS = "- le carre complet n'est pas encore dessine"


def _load(lang):
    return get_prompt_loader().load(
        "convergence.md",
        lang=lang,
        step_description="Appuyer sur la touche Entree",
        expected_result="Ouvrir Paint puis dessiner un carre complet",
        actual_result="[REPONSE] Paint est ouvert.",
        tool_status="UNKNOWN",
        alignment_note="",
        world_snapshot="Paint ouvert",
        world_warnings="",
        plan_gaps=_GAPS,
    )


def test_scope_is_the_mission_not_the_last_step():
    assert "Tu juges la MISSION" in _load("fr")
    assert "Tu juges la MISSION" in _load("base")
    assert "You judge the MISSION" in _load("en")


def test_rule_4_tolerance_never_covers_a_gap():
    fr = _load("fr")
    assert "JAMAIS une lacune" in fr
    assert "prime sur la règle 4" in fr
    en = _load("en")
    assert "NEVER a gap" in en
    assert "overrides rule 4" in en


def test_gaps_are_still_injected():
    for lang in ("base", "en", "fr"):
        assert "carre complet" in _load(lang), lang

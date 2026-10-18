"""BUG B (P0, idée 1) — le planner déclare « ce qu'il change vs la tentative
précédente » ; le juge de plans le lit et le confronte au plan réel.

Évite les faux refus « plan identique » alors que le plan a changé subtilement
(ex : ajout d'un `waiting_ms` invisible autrement). Complète BUG-A : A corrige
la cécité, ce champ guide la lecture.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.prompt_loader import get_prompt_loader

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_plan_accepte_change_vs_previous():
    p = Plan(
        goal="g",
        steps=[PlanStep(id="step_1", description="d", type=StepType.DIRECT_ANSWER,
                        expected_result="ok")],
        change_vs_previous="ajout d'une attente de 5 s",
    )
    assert p.change_vs_previous == "ajout d'une attente de 5 s"
    assert p.model_dump()["change_vs_previous"] == "ajout d'une attente de 5 s"


def test_template_affiche_le_changement():
    out = get_prompt_loader().load(
        "plan_validation.md", lang="en", goal="g", plan_summary="- step_1",
        declared_change="added a 5 s wait after launch")
    assert "added a 5 s wait after launch" in out
    assert "CHANGE DECLARED BY THE PLANNER" in out


def test_template_sans_changement_pas_de_section():
    out = get_prompt_loader().load(
        "plan_validation.md", lang="en", goal="g", plan_summary="- step_1",
        declared_change=None)
    assert "CHANGE DECLARED BY THE PLANNER" not in out


def test_planner_md_demande_le_champ():
    for lang in ("base", "en", "fr"):
        path = os.path.join(_ROOT, "prompts", lang, "planner.md")
        with open(path, encoding="utf-8") as f:
            assert "change_vs_previous" in f.read()

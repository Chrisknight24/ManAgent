"""Lot 2 : prompts adaptes rendus en 3 langues, variables branchees. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader
from core.plan_validator import PlanValidator

LANGS = ("base", "en", "fr")


def _load(name, lang, **kwargs):
    return get_prompt_loader().load(name, lang=lang, **kwargs)


def _tools():
    return [{"name": "press_key", "kind": "action",
             "description": "presser une touche",
             "parameters": {"type": "object", "properties": {}}}]


def test_planner_renders_all_langs_without_leftovers():
    for lang in LANGS:
        out = _load("planner.md", lang, goal="ouvrir bloc-notes. Réponds en français.",
                    strategy="simple", context="aucun", advice="",
                    variable_registry={}, tools=_tools(), skills="",
                    model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "{{" not in out, lang
        assert "CHECKLIST" in out, lang
        assert "perceive_understand" in out, lang


def test_feasibility_renders_all_langs():
    for lang in LANGS:
        out = _load("feasibility.md", lang, goal="g", advice="", context="c",
                    similar_missions="", tools="- press_key : touche",
                    tools_guidance="", skills="", registry="")
        assert "{{" not in out, lang
        assert "is_possible" in out, lang


def test_validation_renders_facts_all_langs():
    for lang in LANGS:
        out = _load(
            "plan_validation.md", lang, goal="g", plan_summary="s", rules="r",
            pattern_warning=None, novelty_assessment="N1",
            repetition_fact="R1", direct_perception_note="P1",
            mission_history_summary="h", declared_irreversible_steps=[],
            hitl_policy="balanced", hitl_policy_text="TEXTE-HITL",
            human_validation_history="x", availability_summary="y")
        assert "{{" not in out, lang
        assert "N1" in out and "R1" in out and "P1" in out and "TEXTE-HITL" in out, lang


def test_orchestrator_renders_what_not_how():
    for lang in LANGS:
        out = _load("orchestrator.md", lang, user_message="bonjour", history="",
                    advice="", model_id="m", supported_modalities=[],
                    unsupported_modalities=[])
        assert "{{" not in out, lang
        assert "QUOI" in out or "WHAT" in out, lang


def test_hitl_policy_text_covers_modes():
    assert "strict" in PlanValidator.hitl_policy_text("strict").lower() or "humain" in PlanValidator.hitl_policy_text("strict")
    assert "autonomous" in PlanValidator.hitl_policy_text("autonomous")
    balanced = PlanValidator.hitl_policy_text("balanced")
    assert "balanced" in balanced
    assert PlanValidator.hitl_policy_text("nawak") == balanced

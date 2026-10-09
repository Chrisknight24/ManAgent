"""Fixes experts post-Tier-3 : hiérarchie, sections, is_crucial, concision (rapide, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader


def _load(name, lang, **kwargs):
    return get_prompt_loader().load(name, lang=lang, **kwargs)


def _presentator_kwargs():
    return dict(goal="g", mission_status="success", final_context="c",
                accumulated_response="r", variable_registry="v",
                session_mood="m", detail_level="brief")


def test_hierarchie_avant_donnees_3_langues():
    for lang in ("base", "en", "fr"):
        out = _load("presentator_output.md", lang=lang, **_presentator_kwargs())
        assert "{{" not in out
        assert "accumulées" in out or "Accumulated" in out
        hi = out.find("RARCH")
        acc = out.find("accumul")
        assert 0 <= hi < acc, lang


def test_pd_autorisee_dans_hierarchie():
    for lang in ("base", "en", "fr"):
        out = _load("presentator_output.md", lang=lang, **_presentator_kwargs())
        assert "Progressive Disclosure" in out
        assert "JAMAIS" in out or "NEVER" in out


def test_etat_monde_section_premier_ordre():
    rendered = {
        "base": "ÉTAT ACTUEL DU MONDE",
        "fr": "ÉTAT ACTUEL DU MONDE",
        "en": "CURRENT WORLD STATE",
    }
    for lang, header in rendered.items():
        out = _load("planner.md", lang=lang, goal="g", strategy="s", context="c",
                    advice="", variable_registry={}, tools=[], skills="",
                    model_id="m", supported_modalities=[], unsupported_modalities=[],
                    world_snapshot="fenetre: Paint")
        assert header in out, lang
        assert out.find(header) < out.find("REGISTRE") or out.find(header) < out.find("REGISTRY"), lang
    out = _load("planner.md", lang="base", goal="g", strategy="s", context="c",
                advice="", variable_registry={}, tools=[], skills="",
                model_id="m", supported_modalities=[], unsupported_modalities=[])
    assert "TAT ACTUEL" not in out


def test_is_crucial_repond_question():
    for lang in ("base", "en", "fr"):
        out = _load("planner.md", lang=lang, goal="g", strategy="s", context="c",
                    advice="", variable_registry={}, tools=[], skills="",
                    model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "RÉPOND" in out or "RPOND" in out or "ANSWERS" in out


def test_concision_sans_abstract():
    for lang in ("base", "fr"):
        out = _load("planner.md", lang=lang, goal="g", strategy="s", context="c",
                    advice="", variable_registry={}, tools=[], skills="",
                    model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "5 à 10 étapes" in out, lang
    en = _load("planner.md", lang="en", goal="g", strategy="s", context="c",
               advice="", variable_registry={}, tools=[], skills="",
               model_id="m", supported_modalities=[], unsupported_modalities=[])
    assert "5 to 10 steps" in en
    assert "abstract_task" not in en.split("8. If history")[-1].split("## VARIABLES")[0]

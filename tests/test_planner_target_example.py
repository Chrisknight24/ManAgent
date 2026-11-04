"""Prompt planner : exemple complet producteur de cible -> consommateur.

Bug reel (mission `008bcdba`, 02:56) : le plan a cree une cible avec
`label` mais sans `output_target_name`, puis a consomme
`$@_target_rectangle` — un nom que personne n'a declare. Le rejet etait
correct (`find_unresolved_plan_refs`), mais le prompt ne montrait aucun
exemple de la chaine complete, et la checklist ne la mentionnait pas.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader


def _load(lang, with_tool_names=True):
    kwargs = dict(
        goal="cliquer sur l'outil rectangle de Paint",
        strategy="s",
        context="c",
        advice="",
        variable_registry={},
        tools=[],
        skills="",
        model_id="m",
        supported_modalities=[],
        unsupported_modalities=[],
        targets_enabled=True,
        target_selection_enabled=False,
    )
    if with_tool_names:
        # P-D (GO 03/11) : le planner injecte les VRAIS noms lus du
        # manifeste hôte (agnostique). Ici des noms de test, pour prouver
        # l'injection et l'absence de placeholder figé.
        kwargs["target_producer_tool"] = "producteur_exemple"
        kwargs["target_consumer_tool"] = "consommateur_exemple"
    return get_prompt_loader().load("planner.md", lang=lang, **kwargs)


def test_target_chain_example_is_present_in_every_language():
    for lang in ("base", "en", "fr"):
        out = _load(lang)
        assert "output_target_name" in out, lang
        # P-D (GO 03/11) : noms d'outils INJECTÉS, jamais de placeholder
        # figé (<TARGET_PRODUCER_TOOL> banni — source d'inventions type
        # $@_data_step_8.target, runs 31/10).
        assert "producteur_exemple" in out, lang
        assert "consommateur_exemple" in out, lang
        assert "<TARGET_PRODUCER_TOOL>" not in out, lang
        assert "<TARGET_ACTION_TOOL>" not in out, lang
        # Le consommateur reprend EXACTEMENT l'alias déclaré.
        assert "$@_controle_nomme" in out, lang
        assert "EXACTEMENT" in out or "EXACTLY" in out, lang


def test_example_tools_fallback_when_names_are_missing():
    # Appelant sans les noms (tests T0, callers partiels) : le template
    # retombe sur une consigne d'indirection (style EXEMPLE 1/2), jamais
    # sur un exemple vide.
    for lang in ("base", "en", "fr"):
        out = _load(lang, with_tool_names=False)
        assert "[produces]" in out, lang
        assert "$@_controle_nomme" in out, lang


def test_checklist_mentions_the_target_alias_chain():
    for lang in ("base", "en", "fr"):
        out = _load(lang)
        checklist = out.split("CHECKLIST", 1)[-1] if "CHECKLIST" in out else out
        assert "output_target_name" in checklist, lang


def test_example_absent_when_targets_are_disabled():
    out = get_prompt_loader().load(
        "planner.md",
        lang="fr",
        goal="g",
        strategy="s",
        context="c",
        advice="",
        variable_registry={},
        tools=[],
        skills="",
        model_id="m",
        supported_modalities=[],
        unsupported_modalities=[],
        targets_enabled=False,
        target_selection_enabled=False,
    )
    assert "$@_rect_tool" not in out

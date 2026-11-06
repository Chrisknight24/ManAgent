"""Prompt planner : exemple complet producteur de cible -> consommateur.

Bug reel (mission `008bcdba`, 02:56) : le plan a cree une cible avec
`label` mais sans `output_target_name`, puis a consomme
`$@_target_rectangle` — un nom que personne n'a declare. Le rejet etait
correct (`find_unresolved_plan_refs`), mais le prompt ne montrait aucun
exemple de la chaine complete, et la checklist ne la mentionnait pas.

Depuis P-D, l'exemple est DERIVE du manifeste (`target_example_context`) :
les noms et les arguments non-cibles viennent du schema de l'hote. Un
exemple qui invente un argument apprend au modele a mal appeler l'outil.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.planner import target_example_context
from core.prompt_loader import get_prompt_loader
from host_sim import HostSim


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
        kwargs["target_source_tool"] = "source_exemple"
        kwargs["target_consumer_tool"] = "consommateur_exemple"
        kwargs["target_consumer_args"] = {
            "action": "click",
            "target": "$TARGET",
        }
    return get_prompt_loader().load("planner.md", lang=lang, **kwargs)


def _manifest():
    return {
        "host_name": "hostsim-example",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "vision",
                "kind": "utility",
                "description": "Lit l'ecran et renvoie des candidates.",
                "parameters": {
                    "type": "object",
                    "properties": {"mode": {"type": "string"}},
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.presets",
                        "mode": "candidates",
                        "scope": "ui_preset",
                        "fields": {"ref": "id", "description": "name"},
                    }],
                },
            },
            {
                "name": "target",
                "kind": "utility",
                "description": "Ancre une cible fraiche depuis une candidate.",
                "parameters": {
                    "type": "object",
                    "properties": {"preset": {"type": "string"}},
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{
                        "param": "preset",
                        "scope": "ui_preset",
                        "required": False,
                    }],
                    "produces": [{
                        "path": "data.target",
                        "mode": "single",
                        "scope": "ui_target",
                        "fields": {"ref": "id", "description": "name"},
                    }],
                },
            },
            {
                "name": "keyboard",
                "kind": "action",
                "description": "Clavier ; cible optionnelle.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["text", "key", "hotkey"],
                        },
                        "text": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["mode"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{
                        "param": "target",
                        "scope": "ui_target",
                        "required": False,
                        "raw": "allow",
                    }],
                },
            },
            {
                "name": "mouse",
                "kind": "action",
                "description": "Action souris sur une cible nommee.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "double_click"],
                        },
                        "target": {"type": "string"},
                    },
                    "required": ["action", "target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "ui_target"}],
                },
            },
        ],
    }


def _tools_manager():
    sim = HostSim(results={})
    asyncio.run(sim.register_manifest(_manifest()))
    return sim.tools_manager


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


def test_example_args_are_json_and_consumed_by_the_consumer():
    for lang in ("base", "en", "fr"):
        out = _load(lang)
        assert '"action": "click"' in out, lang
        assert '"target": "$TARGET"' in out, lang


def test_example_tools_fallback_when_names_are_missing():
    # Appelant sans les noms (tests T0, callers partiels) : le template
    # retombe sur une consigne d'indirection (style EXEMPLE 1/2), jamais
    # sur un exemple vide ni sur un rendu casse.
    for lang in ("base", "en", "fr"):
        out = _load(lang, with_tool_names=False)
        assert "[produces]" in out, lang
        assert "$@_controle_nomme" in out, lang
        assert "$TARGET" in out, lang


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


def test_target_example_context_derives_a_valid_call_from_the_manifest():
    """Producteur, source, consommateur et arguments viennent du manifeste.

    Aucun nom d'hote n'est suppose : l'exemple doit etre directement
    executable. Les arguments non-cibles sont recopies du schema (premiere
    valeur `enum` ou `default`), jamais inventes.

    Le manifeste contient aussi `keyboard` (cible optionnelle, moins de
    proprietes que `mouse`) : l'exemple doit quand meme choisir l'action dont
    la cible est REQUISE, sinon il enseignerait `keyboard mode=text` pour un
    clic.
    """
    tm = _tools_manager()
    context = target_example_context(tm)

    assert context["target_producer_tool"] == "target"
    assert context["target_source_tool"] == "vision"
    assert context["target_consumer_tool"] == "mouse"

    args = context["target_consumer_args"]
    assert args["target"] == "$TARGET"
    assert args["action"] == "click"

    # Tout argument de l'exemple existe dans le schema du consommateur.
    schema = tm._tools["mouse"]["parameters"]
    assert set(args) <= set(schema["properties"])
    # Et tous les parametres requis sont remplis.
    assert set(schema["required"]) <= set(args)
    # JSON serialisable : le prompt le rend via `tojson`.
    assert json.loads(json.dumps(args)) == args


def test_target_example_context_falls_back_without_manifest():
    context = target_example_context(None)
    assert "[produces]" in context["target_producer_tool"]
    assert context["target_consumer_args"] == {"<paramètre cible>": "$TARGET"}

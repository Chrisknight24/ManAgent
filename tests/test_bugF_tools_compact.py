"""BUG F bloc 3 — OUTILS : noms gardés, schémas d'arguments COMPACTÉS.

Le planner inlinait `tool.parameters | tojson` (schéma JSON complet : bruit +
tokens). On rend une signature compacte (noms, `*` requis, `=enum`, type,
description courte). Les NOMS d'outils restent intacts (contrat).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import args_signature, get_prompt_loader

_TOOLS = [
    {
        "name": "file_move",
        "kind": "action",
        "source": "external",
        "description": "Déplace un fichier.",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "chemin source"},
                "dst": {"type": "string", "description": "chemin destination"},
                "mode": {"type": "string", "enum": ["copy", "move"]},
            },
            "required": ["src", "dst"],
        },
    }
]


def _render():
    return get_prompt_loader().load(
        "planner.md", lang="base", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, skills="", model_id="m",
        supported_modalities=[], unsupported_modalities=[], tools=_TOOLS)


def test_signature_basique():
    sig = args_signature(_TOOLS[0]["parameters"])
    assert "src*:string" in sig
    assert "dst*:string" in sig
    assert "mode=copy|move" in sig
    assert "chemin source" in sig


def test_signature_tolerant():
    assert args_signature(None) == ""
    assert args_signature({}) == ""
    assert args_signature({"properties": {}}) == ""
    assert args_signature("pas un dict") == ""


def test_signature_ne_tronque_pas_les_descriptions():
    """Un argument cible porte son contrat dans sa description : on la garde."""
    long_target = (
        "Cible obligatoire : nom local `$@_...` ou ancre sémantique. Sans "
        "ancrage, rien n'est sûr : ne devine jamais une case ni des coordonnées."
    )
    params = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": long_target},
            "clear_first": {
                "type": "boolean",
                "description": (
                    "Vide le champ avant de taper. Utile quand la valeur "
                    "existante doit disparaître au lieu d'être complétée."
                ),
            },
        },
        "required": ["target"],
    }
    sig = args_signature(params)
    assert long_target in sig
    assert "Vide le champ avant de taper" in sig
    assert "…" not in sig


def test_signature_replie_les_sauts_de_ligne():
    sig = args_signature({
        "properties": {"x": {"type": "string", "description": "ligne 1\nligne 2"}}
    })
    assert "ligne 1 ligne 2" in sig
    assert "\n" not in sig


def test_planner_garde_les_noms_et_compacte_les_args():
    out = _render()
    assert "[file_move]" in out           # nom d'outil intact
    assert "src*:string" in out
    assert '"properties"' not in out      # plus de schéma JSON brut
    assert '"required"' not in out

"""Conformance du manifeste reel CondiFlow (vision + target + mouse/keyboard).

Ces bancs figent le contrat declare par l'hote le 06/11/2026 et rejouent les
trois flux annonces dans `host_to_brain.md` §6 :

  - candidate locale -> `target preset=<nom local>` -> `mouse` ;
  - ancre brute `cell=B3` -> `target cell=B3` -> `mouse` ;
  - sonde `focus_candidate` -> nouvelle decision -> `target` -> `mouse`.

Le cerveau ne connait ni `cell`, ni `grid`, ni `preset` : tout vient du
`target_contract`. Les assertions portent sur ce qui part reellement vers
l'hote (arguments resolus) et sur ce qui reste visible du LLM (nom local).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.target_contract import parse_target_contract
from tools import internal_tools as IT


_ATTRIBUTES = {
    "role": "role",
    "rect": "rect",
    "cells": "cells",
    "interactive": "interactive",
    "focused": "focused",
}
_PRESET_FIELDS = {
    "ref": "id",
    "description": "name",
    "attributes": _ATTRIBUTES,
}
_CONSUMES = [
    {"param": "target", "scope": "target", "required": False},
    {"param": "preset", "scope": "ui_preset", "required": False, "raw": "deny"},
    {"param": "cell", "scope": "cell", "required": False, "raw": "allow"},
]


def real_manifest():
    """Sous-ensemble exact du manifeste CondiFlow (blocs du 06/11/2026)."""
    return {
        "host_name": "condiflow-conformance",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "vision",
                "role": "Vision",
                "kind": "perception",
                "description": "voir, attendre, inspecter",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "target": {"type": "string"},
                        "cell": {"type": "string"},
                        "preset": {"type": "string"},
                        "with_image": {"type": "boolean"},
                        "max_presets": {"type": "integer"},
                        "max_marks": {"type": "integer"},
                        "focus_hint": {"type": "string"},
                    },
                    "required": ["mode"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": _CONSUMES,
                    "produces": [
                        {
                            "path": "data.presets",
                            "mode": "candidates",
                            "scope": "ui_preset",
                            "fields": _PRESET_FIELDS,
                        },
                        {
                            "path": "data.elements",
                            "mode": "candidates",
                            "scope": "ui_preset",
                            "fields": _PRESET_FIELDS,
                        },
                    ],
                    "disclosure": {
                        "version": 1,
                        "views": [
                            {
                                "name": "grid",
                                "path": "data.grid",
                                "description": "Grille de cellules de l'ecran.",
                            }
                        ],
                        "anchors": [
                            {
                                "kind": "cell",
                                "param": "cell",
                                "description": "Cellule brute de la grille.",
                            }
                        ],
                        "probes": [
                            {
                                "id": "widen",
                                "kind": "widen",
                                "description": "Relit l'ecran avec un indice plus large.",
                                "tool": "vision",
                                "args": {
                                    "mode": "annotated",
                                    "max_presets": 40,
                                    "max_marks": 60,
                                },
                                "hint_param": "focus_hint",
                                "produces": ["candidates", "grid"],
                            },
                            {
                                "id": "focus_candidate",
                                "kind": "focus",
                                "description": "Inspecte une cible locale deja vue.",
                                "tool": "vision",
                                "args": {"mode": "inspect", "with_image": True},
                                "subject": {"kind": "local", "param": "preset"},
                                "produces": ["candidates", "grid"],
                            },
                            {
                                "id": "inspect_cell",
                                "kind": "inspect",
                                "description": "Inspecte une cellule de la grille.",
                                "tool": "vision",
                                "args": {"mode": "inspect", "with_image": True},
                                "subject": {"kind": "cell", "param": "cell"},
                                "produces": ["candidates", "grid"],
                            },
                        ],
                    },
                },
            },
            {
                "name": "target",
                "role": "Cible",
                "kind": "utility",
                "description": "nomme une cible reutilisable",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cell": {"type": "string"},
                        "preset": {"type": "string"},
                        "target": {"type": "string"},
                        "kind": {"type": "string"},
                        "label": {"type": "string"},
                        "padding": {"type": "integer"},
                        "frame_id": {"type": "string"},
                    },
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": _CONSUMES,
                    "produces": [
                        {
                            "path": "data.target",
                            "mode": "single",
                            "scope": "target",
                            "fields": {
                                "ref": "ref",
                                "description": "label",
                                "correlation_id": "corr",
                                "generation": "gen",
                            },
                        }
                    ],
                },
            },
            {
                "name": "mouse",
                "role": "Souris",
                "kind": "action",
                "description": "clique une cible nommee",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["action", "target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [
                        {"param": "target", "scope": "target", "required": True}
                    ],
                },
            },
            {
                "name": "keyboard",
                "role": "Clavier",
                "kind": "action",
                "description": "frappe une touche, avec cible optionnelle",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "key": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["mode"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [
                        {
                            "param": "target",
                            "scope": "target",
                            "required": False,
                            "raw": "allow",
                        }
                    ],
                },
            },
        ],
    }


class ScriptedLlm:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        decision = self.decisions.pop(0)
        if callable(decision):
            decision = decision(prompt)
        return schema(**decision)


def _local_key(prompt, needle):
    for line in prompt.splitlines():
        line = line.strip()
        if line.startswith("- ") and needle in line:
            return line[2:].split(":", 1)[0].strip()
    raise AssertionError(f"cible {needle!r} absente du prompt:\n{prompt}")


def _grid_and_presets(elements=False):
    data = {"grid": {"tile_px": 100, "cols": 12, "rows": 8, "origin": [0, 0]}}
    item = {
        "id": "t1",
        "name": "Bouton OK",
        "role": "Button",
        "rect": [10, 10, 110, 40],
        "cells": ["B1"],
        "interactive": True,
        "focused": False,
    }
    data["elements" if elements else "presets"] = [item]
    return {"result": True, "data": data}


def _make_sim():
    from host_sim import HostSim

    def vision(args):
        return _grid_and_presets(elements=args.get("mode") == "inspect")

    def target(_args):
        return {
            "result": True,
            "data": {
                "target": {
                    "ref": {"kind": "ui", "id": "3"},
                    "label": "Bouton OK",
                    "corr": "c1",
                    "gen": 1,
                }
            },
        }

    sim = HostSim(results={
        "vision": vision,
        "target": target,
        "mouse": {"result": True, "data": {"clicked": True}},
        "keyboard": {"result": True, "data": {"typed": True}},
    })
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(real_manifest()))
    return sim


def _run(plan_args, llm):
    sim = _make_sim()
    sim.runtime_state._tools_llm = llm
    out = asyncio.run(IT.perceive_action(plan_args, sim.runtime_state))
    return sim, out, llm


_PLAN = {
    "question": "clique sur OK",
    "source_tool": "vision",
    "source_args": {"mode": "image"},
    "target_producer": "target",
    "target_args": {},
    "action_tool": "mouse",
    "action_args": {"action": "click", "target": "$TARGET"},
}


def _call(sim, tool):
    return [c for c in sim.host_calls if c["tool_name"] == tool][0]


def test_contrat_reel_est_accepte_et_generique():
    vision = real_manifest()["tools"][0]
    target = real_manifest()["tools"][1]
    parsed_vision = parse_target_contract(
        vision["target_contract"],
        tool_name="vision",
        parameters_schema=vision["parameters"],
    )
    parsed_target = parse_target_contract(
        target["target_contract"],
        tool_name="target",
        parameters_schema=target["parameters"],
    )
    assert parsed_vision.ok, parsed_vision.errors
    assert parsed_target.ok, parsed_target.errors

    contract = parsed_vision.contract
    assert [v.name for v in contract.disclosure.views] == ["grid"]
    assert [a.kind for a in contract.disclosure.anchors] == ["cell"]
    assert [p.id for p in contract.disclosure.probes] == [
        "widen", "focus_candidate", "inspect_cell",
    ]
    attributes = contract.producers[0].attributes
    assert attributes["role"] == "role"
    assert attributes["cells"] == "cells"
    # Aucun vocabulaire hote n'est impose par le cerveau : les kinds sont lus.
    assert contract.disclosure.probes[1].subject.kind == "local"
    assert contract.disclosure.probes[2].subject.kind == "cell"


def test_candidate_locale_vers_mouse():
    llm = ScriptedLlm([
        lambda prompt: {
            "target_key": _local_key(prompt, "Bouton OK"),
            "target_kind": "local",
            "reason": "candidate visible",
        }
    ])
    sim, out, _ = _run(_PLAN, llm)
    assert out["result"] is True, out

    target_call = _call(sim, "target")
    # Le LLM n'a vu qu'un nom local ; la membrane egress a envoye la ref brute.
    assert target_call["arguments"]["preset"] == "t1"
    assert target_call["arguments_display"]["preset"].startswith("target_")
    assert _call(sim, "mouse")["arguments"]["target"] == "ui:3"
    assert '"role"' in llm.prompts[0]
    assert '"cells"' in llm.prompts[0]


def test_ancre_cellulaire_vers_mouse():
    llm = ScriptedLlm([
        {
            "target_key": "B3",
            "target_kind": "anchor",
            "anchor_kind": "cell",
            "reason": "cellule lue dans la grille",
        }
    ])
    sim, out, _ = _run(_PLAN, llm)
    assert out["result"] is True, out
    assert _call(sim, "target")["arguments"]["cell"] == "B3"
    assert _call(sim, "mouse")["arguments"]["target"] == "ui:3"
    assert '"kind": "cell"' in llm.prompts[0]


def test_sonde_focus_candidate_puis_decision():
    llm = ScriptedLlm([
        lambda prompt: {
            "target_key": "",
            "target_kind": "local",
            "reason": "creuser la candidate",
            "discovery_request": {
                "goal": "identifier le bouton OK",
                "data_type": "targeting",
                "targets": [_local_key(prompt, "Bouton OK")],
                "technical_goals": ["focus_candidate"],
            },
        },
        lambda prompt: {
            "target_key": _local_key(prompt, "Bouton OK"),
            "target_kind": "local",
            "reason": "candidate confirmee",
        },
    ])
    sim, out, _ = _run(_PLAN, llm)
    assert out["result"] is True, out

    inspections = [
        c for c in sim.host_calls
        if c["tool_name"] == "vision" and c["arguments"].get("mode") == "inspect"
    ]
    assert len(inspections) == 1
    # La sonde `local` est resolue par la membrane : l'hote recoit t1.
    assert inspections[0]["arguments"]["preset"] == "t1"
    assert len(llm.prompts) == 2
    assert _call(sim, "mouse")["arguments"]["target"] == "ui:3"


def test_sonde_inspect_cell_puis_ancre():
    llm = ScriptedLlm([
        {
            "target_key": "",
            "target_kind": "local",
            "reason": "creuser la cellule B3",
            "discovery_request": {
                "goal": "voir ce que contient B3",
                "data_type": "targeting",
                "targets": ["B3"],
                "technical_goals": ["inspect_cell"],
            },
        },
        {
            "target_key": "B3",
            "target_kind": "anchor",
            "anchor_kind": "cell",
            "reason": "cellule inspectee",
        },
    ])
    sim, out, _ = _run(_PLAN, llm)
    assert out["result"] is True, out
    inspections = [
        c for c in sim.host_calls
        if c["tool_name"] == "vision" and c["arguments"].get("mode") == "inspect"
    ]
    assert inspections[0]["arguments"]["cell"] == "B3"
    assert _call(sim, "target")["arguments"]["cell"] == "B3"
    assert _call(sim, "mouse")["arguments"]["target"] == "ui:3"


def test_keyboard_raw_allow_laisse_none_et_resout_un_nom_local():
    # `raw: allow` sur un appel direct : un litteral inconnu du registre
    # part tel quel (opt-out global `none`).
    sim = _make_sim()
    out = asyncio.run(sim.orchestrator.execute_tool(
        "keyboard", {"mode": "key", "key": "enter", "target": "none"}
    ))
    assert out
    assert _call(sim, "keyboard")["arguments"]["target"] == "none"

    llm_local = ScriptedLlm([
        lambda prompt: {
            "target_key": _local_key(prompt, "Bouton OK"),
            "target_kind": "local",
            "reason": "candidate visible",
        }
    ])
    plan_local = dict(_PLAN)
    plan_local["action_tool"] = "keyboard"
    plan_local["action_args"] = {
        "mode": "key", "key": "enter", "target": "$TARGET",
    }
    sim, out, _ = _run(plan_local, llm_local)
    assert out["result"] is True, out
    # Un nom local connu reste resolu malgre `raw: allow`.
    assert _call(sim, "keyboard")["arguments"]["target"] == "ui:3"

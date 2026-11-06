"""Bancs multi-hotes : le meme cerveau, des mondes completement differents.

Chaque hote declare ses propres `scope`, `anchors`, `views` et sondes. Le
cerveau ne connait que `local` et `anchor` ; il ne code en dur ni `cell`, ni
`grid`, ni `preset`, ni aucun nom d'outil.

Hotes simules :
  - calculatrice deterministe : aucune perception, seulement des touches ;
  - robot : articulations avec attributs (angle, bornes) ;
  - IDE : fichiers et symboles, ancre brute `file`.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import internal_tools as IT


class _ScriptedLlm:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        decision = self.decisions.pop(0) if self.decisions else {"no_target": True}
        if callable(decision):
            decision = decision(prompt)
        if isinstance(decision, dict):
            return schema(**decision)
        return decision


def _local_key_from_prompt(prompt: str, needle: str) -> str:
    for line in prompt.splitlines():
        line = line.strip()
        if line.startswith("- ") and needle in line:
            return line[2:].split(":", 1)[0].strip()
    raise AssertionError(f"cible {needle!r} absente du prompt")


# ---------------------------------------------------------------------------
# 1. Calculatrice deterministe : aucune perception, aucun candidat visuel.
# ---------------------------------------------------------------------------

def _calculator_manifest():
    return {
        "host_name": "calc-deterministe",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "touches",
                "kind": "utility",
                "description": "Liste les touches disponibles (aucune perception).",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.keys",
                        "mode": "candidates",
                        "scope": "calc_key",
                        "fields": {"ref": "id", "description": "label"},
                    }],
                },
            },
            {
                "name": "appuyer",
                "kind": "action",
                "description": "Appuie sur une touche.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "key", "scope": "calc_key"}],
                },
            },
        ],
    }


def test_hote_deterministe_sans_perception():
    from host_sim import HostSim

    manifest = _calculator_manifest()
    assert not [
        tool for tool in manifest["tools"] if tool.get("kind") == "perception"
    ], "ce banc doit prouver qu'aucun outil de perception n'est requis"

    results = {
        "touches": {
            "result": True,
            "data": {"keys": [
                {"id": "k7", "label": "7"},
                {"id": "kplus", "label": "+"},
            ]},
        },
        "appuyer": {"result": True, "data": {"pressed": True}},
    }
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(manifest))

    llm = _ScriptedLlm([
        lambda prompt: {
            "target_key": _local_key_from_prompt(prompt, "7"),
            "target_kind": "local",
            "reason": "la touche 7 est listee",
        },
    ])
    sim.runtime_state._tools_llm = llm

    out = asyncio.run(IT.perceive_action({
        "question": "appuie sur la touche 7",
        "source_tool": "touches",
        "source_args": {},
        "action_tool": "appuyer",
        "action_args": {"key": "$TARGET"},
    }, sim.runtime_state))

    assert out["result"] is True, out
    call = [c for c in sim.host_calls if c["tool_name"] == "appuyer"][0]
    assert call["arguments"]["key"] == "k7"
    # Le selecteur n'a jamais vu de nom d'outil hote inventer une cible.
    assert "appuyer" not in llm.prompts[0]


# ---------------------------------------------------------------------------
# 2. Robot : articulations, attributs libres, cible locale.
# ---------------------------------------------------------------------------

def _robot_manifest():
    return {
        "host_name": "robot-bras",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "articulations",
                "kind": "perception",
                "description": "Etat des articulations du bras.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.joints",
                        "mode": "candidates",
                        "scope": "joint",
                        "fields": {
                            "ref": "id",
                            "description": "name",
                            "attributes": {
                                "angle": "angle",
                                "min": "min_angle",
                                "max": "max_angle",
                            },
                        },
                    }],
                },
            },
            {
                "name": "bouger",
                "kind": "action",
                "description": "Deplace une articulation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "joint": {"type": "string"},
                        "angle": {"type": "number"},
                    },
                    "required": ["joint", "angle"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "joint", "scope": "joint"}],
                },
            },
        ],
    }


def test_hote_robot_articulations_et_attributs():
    from host_sim import HostSim

    results = {
        "articulations": {
            "result": True,
            "data": {"joints": [
                {"id": "j0", "name": "base", "angle": 0,
                 "min_angle": -90, "max_angle": 90},
                {"id": "j2", "name": "coude", "angle": 12,
                 "min_angle": 0, "max_angle": 135},
            ]},
        },
        "bouger": {"result": True, "data": {"moved": True}},
    }
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(_robot_manifest()))

    llm = _ScriptedLlm([
        lambda prompt: {
            "target_key": _local_key_from_prompt(prompt, "coude"),
            "target_kind": "local",
            "reason": "articulation coude",
        },
    ])
    sim.runtime_state._tools_llm = llm

    out = asyncio.run(IT.perceive_action({
        "question": "bouge le coude a 45 degres",
        "source_tool": "articulations",
        "source_args": {},
        "action_tool": "bouger",
        "action_args": {"joint": "$TARGET", "angle": 45},
    }, sim.runtime_state))

    assert out["result"] is True, out
    call = [c for c in sim.host_calls if c["tool_name"] == "bouger"][0]
    assert call["arguments"]["joint"] == "j2"
    assert call["arguments"]["angle"] == 45
    prompt = llm.prompts[0]
    assert '"angle"' in prompt and '"max"' in prompt


# ---------------------------------------------------------------------------
# 3. IDE : ancre brute `file` produite par un utilitaire, cible locale ensuite.
# ---------------------------------------------------------------------------

def _ide_manifest():
    return {
        "host_name": "ide-code",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "explorateur",
                "kind": "utility",
                "description": "Lit l'arborescence du projet.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.files",
                        "mode": "candidates",
                        "scope": "repo_file",
                        "fields": {"ref": "id", "description": "path"},
                    }],
                    "disclosure": {
                        "version": 1,
                        "views": [
                            {"name": "tree", "path": "data.tree"},
                        ],
                        "anchors": [
                            {"kind": "file", "param": "path"},
                        ],
                        "probes": [],
                    },
                },
            },
            {
                "name": "resoudre_fichier",
                "kind": "utility",
                "description": "Transforme un chemin en cible fraiche.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{
                        "param": "path", "scope": "file", "required": False,
                        "raw": "allow",
                    }],
                    "produces": [{
                        "path": "data.target",
                        "mode": "single",
                        "scope": "file_target",
                        "fields": {"ref": "id", "description": "name"},
                    }],
                },
            },
            {
                "name": "ouvrir_fichier",
                "kind": "action",
                "description": "Ouvre une cible fichier.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "file_target"}],
                },
            },
        ],
    }


def test_hote_ide_ancre_fichier_puis_cible_locale():
    from host_sim import HostSim

    results = {
        "explorateur": {
            "result": True,
            "data": {
                "files": [{"id": "f1", "path": "src/main.py"}],
                "tree": ["src/", "src/main.py", "README.md"],
            },
        },
        "resoudre_fichier": {
            "result": True,
            "data": {"target": {"id": "f2", "name": "main.py"}},
        },
        "ouvrir_fichier": {"result": True, "data": {"opened": True}},
    }
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(_ide_manifest()))

    llm = _ScriptedLlm([
        {
            "target_key": "src/main.py",
            "target_kind": "anchor",
            "anchor_kind": "file",
            "reason": "chemin lu dans l'arborescence",
        },
    ])
    sim.runtime_state._tools_llm = llm

    out = asyncio.run(IT.perceive_action({
        "question": "ouvre src/main.py",
        "source_tool": "explorateur",
        "source_args": {},
        "target_producer": "resoudre_fichier",
        "target_args": {},
        "action_tool": "ouvrir_fichier",
        "action_args": {"target": "$TARGET"},
    }, sim.runtime_state))

    assert out["result"] is True, out
    resolve_call = [
        c for c in sim.host_calls if c["tool_name"] == "resoudre_fichier"
    ][0]
    assert resolve_call["arguments"]["path"] == "src/main.py"
    open_call = [
        c for c in sim.host_calls if c["tool_name"] == "ouvrir_fichier"
    ][0]
    assert open_call["arguments"]["target"] == "f2"
    assert out["data"]["target"]["kind"] == "local"
    # La vue nommee et l'ancre declaree sont visibles dans le prompt.
    assert "src/main.py" in llm.prompts[0]
    assert '"kind": "file"' in llm.prompts[0]

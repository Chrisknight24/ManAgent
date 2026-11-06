"""Ciblage visible : attributs, vues nommees, ancres et sondes `disclosure`.

Le banc reste AGNOSTIQUE : aucun nom d'outil/champ hote code en dur dans le
cerveau. Tout vient du contrat declare par le faux hote. `local` et `anchor`
sont les deux seuls concepts du cerveau ; `cell`, `grid`, `file`, `key`...
sont des vocabulaires d'hote.
"""

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discovery.models import DiscoveryRequest
from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload
from core.targeting import ObservedWorld, resolve_target
from core.targets import TargetRegistry
from tools import internal_tools as IT


def _contract(raw, tool_name="scan"):
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


def _scan_contract(*, disclosure=None, attributes=None):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {
                "ref": "id",
                "description": "name",
                **({"attributes": attributes} if attributes else {}),
            },
        }],
    }
    if disclosure is not None:
        raw["disclosure"] = disclosure
    return _contract(raw)


class _FakeManager:
    def __init__(self, contracts=None, payloads=None):
        self.contracts = contracts or {}
        self.payloads = payloads or {}
        self.calls = []

    def get_target_contract(self, name):
        return self.contracts.get(name)

    def get_tool_returns(self, name):
        return []

    async def register_typed_assets(self, assets, session_id="default_session"):
        return {asset.target_id: asset.target_id for asset in assets}

    async def execute_tool(self, name, args):
        self.calls.append((name, dict(args)))
        result = self.payloads.get(name, {"result": True, "data": {}})
        if callable(result):
            result = result(dict(args))
        return json.dumps(result)


def _runtime(manager, registry, *, llm=None, variables=None):
    return types.SimpleNamespace(
        tools_manager=manager,
        target_registry=registry,
        _solver_registry_for_tools=variables if variables is not None else {},
        _tools_llm=llm,
        language="fr",
    )


class _ScriptedLlm:
    """Joue une suite de decisions et retient les prompts echanges."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []
        self.calls = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        self.calls.append(kwargs)
        decision = self.decisions.pop(0) if self.decisions else {"no_target": True}
        if callable(decision):
            decision = decision(prompt)
        if isinstance(decision, dict):
            return schema(**decision)
        return decision


def test_contract_parse_attributes_views_anchors_et_kind_libre():
    contract = _scan_contract(
        attributes={"role": "role", "rect": "bounds", "cells": "meta.cells"},
        disclosure={
            "version": 1,
            "views": [
                {"name": "grid", "path": "data.grid", "description": "Grille"},
                {"name": "tree", "path": "data.tree"},
            ],
            "anchors": [
                {"kind": "cell", "param": "cell", "description": "Cellule"},
                {"kind": "file", "param": "path"},
            ],
            "probes": [
                {
                    "id": "widen",
                    "kind": "widen",
                    "description": "Elargir la capture",
                    "tool": "scan",
                    "args": {"mode": "inspect"},
                    "hint_param": "focus_hint",
                    "produces": ["candidates"],
                },
                {
                    "id": "inspect_cell",
                    "kind": "inspect",
                    "tool": "scan",
                    "args": {"mode": "inspect"},
                    "subject": {"kind": "cell", "param": "cell"},
                },
                {
                    "id": "host_custom",
                    "kind": "read_sensor",
                    "tool": "scan",
                },
            ],
        },
    )
    producer = contract.producers[0]
    assert producer.attributes == {
        "role": "role",
        "rect": "bounds",
        "cells": "meta.cells",
    }
    disclosure = contract.disclosure
    assert disclosure is not None
    assert [view.name for view in disclosure.views] == ["grid", "tree"]
    assert disclosure.view("grid").path == "data.grid"
    assert disclosure.anchor("cell").param == "cell"
    assert disclosure.anchor("file").param == "path"
    assert disclosure.anchor_kinds() == ("cell", "file")
    assert disclosure.probe("widen").hint_param == "focus_hint"
    assert disclosure.probe("inspect_cell").subject.kind == "cell"
    # `kind` de sonde : vocabulaire hote libre, jamais un enum du cerveau.
    assert disclosure.probe("host_custom").kind == "read_sensor"


def test_attributes_projetees_et_visibles_dans_le_monde():
    contract = _scan_contract(attributes={"role": "role", "rect": "bounds"})
    registry = TargetRegistry()
    projected, count = project_target_payload(registry, contract, {
        "result": True,
        "data": {"candidates": [{
            "id": "ref-ok",
            "name": "Bouton OK",
            "role": "button",
            "bounds": {"x": 10, "y": 20, "w": 80, "h": 30},
        }]},
    })
    assert count == 1
    candidate = projected["data"]["candidates"][0]
    assert candidate["role"] == "button"
    assert candidate["rect"] == {"x": 10, "y": 20, "w": 80, "h": 30}
    assert "ref-ok" not in json.dumps(projected)

    world = ObservedWorld.from_payload(registry, contract, projected)
    assert len(world.candidates) == 1
    assert world.candidates[0].attributes["role"] == "button"
    assert world.candidates[0].attributes["rect"]["w"] == 80


def test_resolve_target_demande_une_sonde_puis_retente():
    contract = _scan_contract(disclosure={
        "version": 1,
        "probes": [{
            "id": "widen",
            "kind": "widen",
            "tool": "scan",
            "args": {"mode": "inspect"},
            "hint_param": "focus_hint",
            "produces": ["candidates"],
        }],
    })
    registry = TargetRegistry()
    initial = project_target_payload(registry, contract, {
        "result": True,
        "data": {"candidates": []},
    })[0]

    manager = _FakeManager(
        contracts={"scan": contract},
        payloads={
            "scan": {
                "result": True,
                "data": {"candidates": [{
                    "id": "ref-ok", "name": "Bouton OK", "role": "button",
                }]},
            },
        },
    )

    def decision(prompt):
        for line in prompt.splitlines():
            line = line.strip()
            if line.startswith("- ") and "Bouton OK" in line:
                return {
                    "target_key": line[2:].split(":", 1)[0].strip(),
                    "target_kind": "local",
                    "reason": "trouve apres inspection",
                }
        return {
            "no_target": False,
            "discovery_request": DiscoveryRequest(
                goal="trouver le bouton OK",
                data_type="targeting",
                targets=[""],
                technical_goals=["widen"],
            ),
        }

    llm = _ScriptedLlm([decision, decision])
    world = ObservedWorld.from_payload(registry, contract, initial)
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le bouton OK",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=2,
    ))

    assert result.ok, result.error_message
    assert result.kind == "local"
    assert result.entry.ref.value == "ref-ok"
    assert len(world.probes) == 1 and world.probes[0].ok
    assert manager.calls[0][0] == "scan"
    # La sonde recoit la consigne naturelle dans son parametre declare.
    assert manager.calls[0][1]["focus_hint"] == "le bouton OK"
    # La seconde decision voit la candidate fraiche.
    assert "Bouton OK" in llm.prompts[-1]


def test_sonde_locale_utilise_le_nom_local_si_consommateur_declare():
    scan = _scan_contract(disclosure={
        "version": 1,
        "probes": [{
            "id": "focus",
            "kind": "focus",
            "tool": "inspect",
            "subject": {"kind": "local", "param": "preset"},
        }],
    })
    inspect = _contract({
        "version": 1,
        "consumes": [{"param": "preset", "scope": "ui_preset"}],
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {"ref": "id", "description": "name"},
        }],
    }, tool_name="inspect")
    registry = TargetRegistry()
    initial = project_target_payload(registry, scan, {
        "result": True,
        "data": {"candidates": [{"id": "t1", "name": "OK"}]},
    })[0]
    local = registry.find_by_ref("t1").local_name

    manager = _FakeManager(
        contracts={"scan": scan, "inspect": inspect},
        payloads={"inspect": {"result": True, "data": {"candidates": []}}},
    )
    llm = _ScriptedLlm([
        {
            "discovery_request": DiscoveryRequest(
                goal="inspecter la cible locale",
                data_type="targeting",
                targets=[local],
                technical_goals=["focus"],
            ),
        },
        {"no_target": True, "reason": "inspection faite"},
    ])
    world = ObservedWorld.from_payload(registry, scan, initial)
    asyncio.run(resolve_target(
        llm=llm,
        question="OK",
        world=world,
        contract=scan,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=2,
    ))

    # Le parametre est un consommateur declare : la membrane egress resoudra
    # le nom local en reference brute au dispatch.
    assert manager.calls == [("inspect", {"preset": local})]


def test_sonde_locale_envoie_la_ref_brute_si_parametre_non_consommateur():
    scan = _scan_contract(disclosure={
        "version": 1,
        "probes": [{
            "id": "focus",
            "kind": "focus",
            "tool": "inspect",
            "subject": {"kind": "local", "param": "preset"},
        }],
    })
    inspect = _contract({
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {"ref": "id", "description": "name"},
        }],
    }, tool_name="inspect")
    registry = TargetRegistry()
    initial = project_target_payload(registry, scan, {
        "result": True,
        "data": {"candidates": [{"id": "t1", "name": "OK"}]},
    })[0]

    manager = _FakeManager(
        contracts={"scan": scan, "inspect": inspect},
        payloads={"inspect": {"result": True, "data": {"candidates": []}}},
    )
    llm = _ScriptedLlm([
        {
            "discovery_request": DiscoveryRequest(
                goal="inspecter la cible locale",
                data_type="targeting",
                targets=[registry.find_by_ref("t1").local_name],
                technical_goals=["focus"],
            ),
        },
        {"no_target": True, "reason": "inspection faite"},
    ])
    world = ObservedWorld.from_payload(registry, scan, initial)
    asyncio.run(resolve_target(
        llm=llm,
        question="OK",
        world=world,
        contract=scan,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=2,
    ))

    assert manager.calls == [("inspect", {"preset": "t1"})]


def test_media_et_vues_nommees_sont_dans_le_prompt_du_selecteur():
    disclosure = {
        "version": 1,
        "views": [
            {"name": "grid", "path": "data.grid", "description": "Grille"},
            {"name": "tree", "path": "data.tree"},
        ],
        "anchors": [{"kind": "cell", "param": "cell"}],
        "probes": [{
            "id": "widen",
            "kind": "widen",
            "tool": "scan",
        }],
    }
    contract = _scan_contract(disclosure=disclosure)
    registry = TargetRegistry()
    payload = project_target_payload(registry, contract, {
        "result": True,
        "data": {
            "candidates": [{"id": "ref-ok", "name": "OK"}],
            "grid": {"rows": 4, "cols": 6, "cells": [{"ref": "r1c1"}]},
            "tree": ["src/main.py"],
        },
    })[0]

    asset = types.SimpleNamespace(
        target_id="inputs://shot.png",
        asset_meta=types.SimpleNamespace(mime_type="image/png", name="shot.png"),
        raw_bytes=b"pixels",
    )
    llm = _ScriptedLlm([{"no_target": True}])
    world = ObservedWorld.from_payload(
        registry, contract, payload, media_assets=[asset]
    )
    asyncio.run(resolve_target(
        llm=llm,
        question="OK",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_FakeManager(contracts={"scan": contract}),
        allow_anchor=True,
        max_rounds=0,
    ))

    prompt = llm.prompts[0]
    assert "image/png" in prompt
    assert "inputs://shot.png" in prompt
    assert "r1c1" in prompt
    assert "src/main.py" in prompt
    assert '"kind": "cell"' in prompt
    assert llm.calls[0]["media_assets"] == [asset]


def test_ancre_inconnue_refusee_honnetement():
    contract = _scan_contract(disclosure={
        "version": 1,
        "anchors": [{"kind": "cell", "param": "cell"}],
    })
    registry = TargetRegistry()
    payload = project_target_payload(registry, contract, {
        "result": True,
        "data": {"candidates": []},
    })[0]
    llm = _ScriptedLlm([{
        "target_key": "whatever",
        "target_kind": "anchor",
        "anchor_kind": "invented_by_llm",
    }])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="?",
        world=ObservedWorld.from_payload(registry, contract, payload),
        contract=contract,
        registry=registry,
        tools_mgr=_FakeManager(contracts={"scan": contract}),
        allow_anchor=True,
        anchor_kinds=["cell"],
        max_rounds=0,
    ))
    assert result.ok is False
    assert result.error_code == "TARGET_ANCHOR_UNSUPPORTED"
    assert "invented_by_llm" in result.error_message


def _cell_manifest():
    """Hote computer-use simule : grille, cellule, vue nommee."""
    return {
        "host_name": "hostsim-cells",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "grid",
                "kind": "utility",
                "description": "Capture une grille et des candidates.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.candidates",
                        "mode": "candidates",
                        "scope": "ui_preset",
                        "fields": {"ref": "id", "description": "name"},
                    }],
                    "disclosure": {
                        "version": 1,
                        "views": [
                            {"name": "grid", "path": "data.grid"},
                        ],
                        "anchors": [
                            {"kind": "cell", "param": "cell"},
                        ],
                        "probes": [],
                    },
                },
            },
            {
                "name": "target",
                "kind": "utility",
                "description": "Produit une cible fraiche depuis une cellule.",
                "parameters": {
                    "type": "object",
                    "properties": {"cell": {"type": "string"}},
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{
                        "param": "cell", "scope": "cell", "required": False,
                        "raw": "allow",
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
                "name": "click",
                "kind": "action",
                "description": "Clique sur une cible nommee.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "ui_target"}],
                },
            },
        ],
    }


def test_ancre_finale_injectee_dans_le_consommateur_brut_du_producteur():
    from host_sim import HostSim

    results = {
        "grid": {
            "result": True,
            "data": {
                "candidates": [{"id": "ref-a", "name": "A"}],
                "grid": {"cells": [{"ref": "r1c2", "label": "OK"}]},
            },
        },
        "target": {
            "result": True,
            "data": {"target": {"id": "ref-cell", "name": "Cellule OK"}},
        },
        "click": {"result": True, "data": {"clicked": True}},
    }
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(_cell_manifest()))

    sim.runtime_state._tools_llm = _ScriptedLlm([
        {
            "target_key": "r1c2",
            "target_kind": "anchor",
            "anchor_kind": "cell",
            "reason": "cellule OK",
        },
    ])

    out = asyncio.run(IT.perceive_action({
        "question": "clique sur la cellule OK",
        "source_tool": "grid",
        "source_args": {},
        "target_producer": "target",
        "target_args": {},
        "action_tool": "click",
        "action_args": {"target": "$TARGET"},
    }, sim.runtime_state))

    assert out["result"] is True, out
    target_call = [c for c in sim.host_calls if c["tool_name"] == "target"][0]
    assert target_call["arguments"]["cell"] == "r1c2"
    click_call = [c for c in sim.host_calls if c["tool_name"] == "click"][0]
    assert click_call["arguments"]["target"] == "ref-cell"
    # Le nom local, jamais la reference brute, reste la seule sortie visible.
    assert out["data"]["target"]["kind"] == "local"


def test_ancre_refusee_si_aucun_consommateur_brut_declare():
    from host_sim import HostSim

    manifest = _cell_manifest()
    for tool in manifest["tools"]:
        if tool["name"] == "target":
            tool["parameters"]["properties"]["preset"] = {"type": "string"}
            tool["target_contract"]["consumes"] = [{
                "param": "preset", "scope": "ui_preset", "required": False,
            }]
    results = {
        "grid": {
            "result": True,
            "data": {
                "candidates": [{"id": "ref-a", "name": "A"}],
                "grid": {"cells": [{"ref": "r1c2", "label": "OK"}]},
            },
        },
    }
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(manifest))
    sim.runtime_state._tools_llm = _ScriptedLlm([
        {
            "target_key": "r1c2",
            "target_kind": "anchor",
            "anchor_kind": "cell",
            "reason": "cellule OK",
        },
    ])

    out = asyncio.run(IT.perceive_action({
        "question": "clique sur la cellule OK",
        "source_tool": "grid",
        "target_producer": "target",
        "target_args": {},
        "action_tool": "click",
        "action_args": {"target": "$TARGET"},
    }, sim.runtime_state))

    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_ANCHOR_UNSUPPORTED"
    assert "ancre" in out["message"].lower()
    assert [c for c in sim.host_calls if c["tool_name"] == "target"] == []

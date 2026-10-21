"""S9 : integration HostSim (generation, updates, purge, rejets)."""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from host_sim import HostSim


def _manifest():
    return {
        "host_name": "hostsim-s9",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "scan",
                "kind": "perception",
                "description": "Scanne les elements visibles.",
                "parameters": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.targets",
                        "mode": "candidates",
                        "scope": "ui_element",
                        "fields": {
                            "ref": "id",
                            "description": "label",
                            "correlation_id": "corr",
                            "generation": "gen",
                        },
                    }],
                },
            },
            {
                "name": "click",
                "kind": "action",
                "description": "Clique une cible.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "ui_element"}],
                },
            },
        ],
    }


def _scan(*targets):
    return {
        "result": True,
        "data": {"targets": [
            {"id": ref, "label": label, "corr": corr, "gen": gen}
            for ref, label, corr, gen in targets
        ]},
    }


async def _host(results):
    sim = HostSim(results=results)
    await sim.register_manifest(_manifest())
    return sim


@pytest.mark.asyncio
async def test_s9_hostsim_generation_superieure_gagne_et_ancienne_ignoree():
    sim = await _host({"scan": [
        _scan(("ref-g1", "Fermer", "c-gen", 1)),
        _scan(("ref-g2", "Fermer", "c-gen", 2)),
        _scan(("ref-stale", "Fermer", "c-gen", 1)),
    ]})

    keys = []
    for _ in range(3):
        projected = await sim.dispatch("scan", {"question": "ou ?"})
        keys.append(projected["data"]["targets"][0]["key"])

    assert len(set(keys)) == 1
    assert sim.runtime_state.target_registry.resolve(keys[0]).value == "ref-g2"

    done = await sim.dispatch("click", {"target": keys[0]})
    assert done["result"] is True
    assert sim.host_calls[-1]["arguments"] == {"target": "ref-g2"}
    assert "ref-g2" not in json.dumps(done, ensure_ascii=False)


@pytest.mark.asyncio
async def test_s9_hostsim_update_pendant_action_puis_refus_ferme():
    sim = await _host({
        "scan": [_scan(("ref-1", "Fermer", "c-1", 1))],
        "click": [
            {
                "result": False,
                "data": None,
                "message": "Element disparu.",
                "target_updates": [{
                    "correlation_id": "c-1",
                    "generation": 2,
                    "op": "invalidate",
                }],
            },
            {"result": True, "data": None},
        ],
    })
    scan = await sim.dispatch("scan", {"question": "ou ?"})
    local = scan["data"]["targets"][0]["key"]

    first = await sim.dispatch("click", {"target": local})
    assert first["dispatched"] is True
    assert sim.runtime_state.target_registry.get(local).active is False

    calls_before = len(sim.host_calls)
    second = await sim.dispatch("click", {"target": local})

    assert second["result"] is False
    assert second["dispatched"] is False
    assert second["error"]["code"] == "TARGET_INACTIVE"
    assert len(sim.host_calls) == calls_before


@pytest.mark.asyncio
async def test_s9_hostsim_purge_entre_scan_et_click_refuse_sans_dispatch():
    sim = await _host({"scan": [_scan(("ref-1", "Fermer", "c-1", 1))]})
    scan = await sim.dispatch("scan", {"question": "ou ?"})
    local = scan["data"]["targets"][0]["key"]

    sim.runtime_state.reset_target_registry()
    calls_before = len(sim.host_calls)
    refused = await sim.dispatch("click", {"target": local})

    assert refused["result"] is False
    assert refused["dispatched"] is False
    assert refused["error"]["code"] == "TARGET_NOT_FOUND"
    assert len(sim.host_calls) == calls_before


@pytest.mark.asyncio
async def test_s9_hostsim_rejette_une_cible_producteur_invalide():
    sim = await _host({"scan": [_scan(
        ("", "Invalide", "c-bad", 1),
        ("ref-good", "Valide", "c-good", 1),
    )]})

    scan = await sim.dispatch("scan", {"question": "ou ?"})

    assert sim.last_target_count == 1
    assert len(scan["data"]["targets"]) == 1
    local = scan["data"]["targets"][0]["key"]
    assert sim.runtime_state.target_registry.find_by_ref("ref-good") is not None
    assert sim.runtime_state.target_registry.find_by_correlation("c-bad") is None

    await sim.dispatch("click", {"target": local})
    assert sim.host_calls[-1]["arguments"] == {"target": "ref-good"}

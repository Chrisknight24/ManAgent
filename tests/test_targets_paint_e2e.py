"""Bout-en-bout protocole : mission Paint (`target` -> `mouse`).

Ce test rejoue le plan reel qui a echoue en production
(`TARGET_NOT_FOUND ... consumer 'target' mixes text and target reference`)
sur le banc d'essai `HostSim` (vrai `ToolsManager`, vrai `TargetRegistry`,
vrai `Orchestrator`). Il verifie la chaine complete :

    manifeste hote -> projection ingress -> liaison alias -> substitution
    -> membrane egress -> dispatch hote (reference brute)

Sans LLM et sans interface graphique : c'est le test script du scenario
utilisateur.
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.executor import Executor
from core.plan_models import PlanStep, StepType
from host_sim import HostSim


# Manifeste calque sur `Agentic/rpahostprovider.h` (CondiFlow) : scope
# `target`, produit `data.target`, consomme `target`.
def _manifest():
    return {
        "host_name": "hostsim-paint",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "target",
                "kind": "utility",
                "description": "Cree une cible reutilisable depuis une case.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cell": {"type": "string"},
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
                    "consumes": [
                        {"param": "target", "scope": "target", "required": False},
                    ],
                    "produces": [{
                        "path": "data.target",
                        "mode": "single",
                        "scope": "target",
                        "fields": {
                            "ref": "ref",
                            "description": "label",
                            "correlation_id": "corr",
                            "generation": "gen",
                        },
                    }],
                },
            },
            {
                "name": "mouse",
                "kind": "action",
                "description": "Une action souris sur une cible nommee.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "target": {"type": "string"},
                        "impact": {"type": "object"},
                        "button": {"type": "string"},
                        "double": {"type": "boolean"},
                    },
                    "required": ["action", "target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "target"}],
                },
            },
        ],
    }


_CELL_REFS = {
    "E4": ("ref-e4-start", "corr-e4", 3),
    "G6": ("ref-g6-end", "corr-g6", 5),
}


def _host_results():
    def _target(args):
        cell = str(args.get("cell") or "")
        ref, corr, gen = _CELL_REFS[cell]
        return {
            "result": True,
            "data": {"target": {
                "ref": ref,
                "label": args.get("label") or cell,
                "corr": corr,
                "gen": gen,
            }},
        }

    return {
        "target": _target,
        "mouse": lambda args: {
            "result": True,
            "data": {"action": args.get("action")},
        },
    }


class _Solver:
    """Solveur minimal : route l'execution vers le vrai HostSim."""

    def __init__(self, sim):
        self.id = "s-paint-e2e"
        self.runtime_state = sim.runtime_state
        self.variable_registry = {}
        self.current_attempt = SimpleNamespace(attempt_number=1)
        self._sim = sim

    async def execute_tool(self, tool_name, arguments, llm=None):
        return await self._sim.orchestrator.execute_tool(tool_name, arguments)


def _step(step_id, tool, args, *, alias=None, expected="true"):
    return PlanStep(
        id=step_id,
        description=tool,
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json=json.dumps(args),
        expected_result=expected,
        output_target_name=alias,
    )


def _paint_plan():
    return [
        _step("step_2", "target", {"cell": "E4", "label": "pt_start"}, alias="pt_start"),
        _step("step_3", "target", {"cell": "G6", "label": "pt_end"}, alias="pt_end"),
        _step("step_4", "mouse", {"action": "press", "target": "$@_pt_start"}, expected="any"),
        _step("step_5", "mouse", {"action": "move", "target": "$@_pt_end"}, expected="any"),
        _step("step_6", "mouse", {"action": "release", "target": "$@_pt_end"}, expected="any"),
    ]


async def _run_plan(sim, steps, solver=None):
    solver = solver or _Solver(sim)
    executor = Executor.__new__(Executor)
    executor.solver = solver
    results = []
    for step in steps:
        node = SimpleNamespace(metadata={})
        ok, output, error = await executor._handle_tool_call(step, node=node)
        results.append((ok, output, error))
        if not ok:
            break
    return solver, results


def _mouse_calls(sim):
    return [call for call in sim.host_calls if call["tool_name"] == "mouse"]


@pytest.mark.asyncio
async def test_paint_press_move_release_recoit_les_references_brutes():
    sim = HostSim(results=_host_results())
    await sim.register_manifest(_manifest())
    registry_events = []
    sim.runtime_state.target_registry.set_observer(
        lambda name, payload: registry_events.append((name, payload))
    )

    solver, results = await _run_plan(sim, _paint_plan())

    assert all(ok for ok, _out, err in results), [err for ok, _o, err in results if not ok]
    assert all(err is None for _ok, _o, err in results)

    # Les deux alias pointent vers des noms locaux distincts et actifs.
    start = sim.runtime_state.target_registry.find_by_ref("ref-e4-start")
    end = sim.runtime_state.target_registry.find_by_ref("ref-g6-end")
    assert start is not None and start.active
    assert end is not None and end.active
    assert solver.variable_registry["pt_start"]["value"] == start.local_name
    assert solver.variable_registry["pt_end"]["value"] == end.local_name
    assert start.local_name != end.local_name

    # L'hote recoit uniquement des references brutes, jamais un `$@_`.
    calls = _mouse_calls(sim)
    assert [call["arguments"]["action"] for call in calls] == ["press", "move", "release"]
    assert [call["arguments"]["target"] for call in calls] == [
        "ref-e4-start", "ref-g6-end", "ref-g6-end",
    ]
    for call in calls:
        assert "$@_" not in json.dumps(call["arguments"])
        assert call["arguments_display"]["target"] in (
            start.local_name, end.local_name,
        )

    # Aucun refus pre-dispatch, aucun `mixes text`.
    # `resolve()` emet aussi `target.bound` (sans alias) a chaque egress :
    # on ne regarde ici que les liaisons d'alias publiees par le producteur.
    alias_bindings = {
        payload.get("alias")
        for name, payload in registry_events
        if name == "target.bound" and payload.get("alias")
    }
    assert alias_bindings == {"pt_start", "pt_end"}
    assert "mixes text" not in json.dumps(sim.events)


@pytest.mark.asyncio
async def test_paint_cible_purgee_refuse_sans_dispatch_puis_retarget_possible():
    sim = HostSim(results=_host_results())
    await sim.register_manifest(_manifest())

    solver, results = await _run_plan(sim, _paint_plan()[:2])
    assert all(ok for ok, _o, err in results), results

    # Purge officielle entre la creation des cibles et le geste.
    sim.runtime_state.reset_target_registry()
    dispatched_before = len(_mouse_calls(sim))

    _solver, results = await _run_plan(sim, _paint_plan()[2:3], solver=solver)

    ok, _output, error = results[0]
    assert ok is True  # l'etape est materialisee, la convergence tranchera
    assert "[pre-dispatch]" in (error or "")
    assert "TARGET_" in (error or "")
    assert len(_mouse_calls(sim)) == dispatched_before  # rien n'est parti

    # Re-target : recreer la cible rend le meme plan rejouable.
    retarget = [_step("step_7", "target", {"cell": "E4", "label": "pt_start"}, alias="pt_start")]
    _solver, results = await _run_plan(sim, retarget, solver=solver)
    assert results[0][0] is True
    assert results[0][2] is None

    _solver, results = await _run_plan(sim, _paint_plan()[2:3], solver=solver)
    assert results[0][0] is True
    assert results[0][2] is None
    assert _mouse_calls(sim)[-1]["arguments"] == {
        "action": "press", "target": "ref-e4-start",
    }

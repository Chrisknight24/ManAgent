"""E2E protocole : references OBJET de l'hote (`{kind,id}`) -> `mouse`.

Le vrai hote CondiFlow renvoie `data.target.ref = {"kind": "img", "id": "..."}`
(`AgentTargetStore::refJson`, agenttargetstore.cpp:155-161) et declare le
parametre consommateur `target` en `type: "string"` (rpahostprovider.h:253).

Ce test verifie la chaine complete sur `HostSim` durci (gate C++ reproduit) :

    target (ref objet) -> ingress -> alias -> egress "kind:id" -> mouse

Sans la serialisation egress, `host_target_gate` renvoie `TARGET_REQUIRED`
et le test echoue : c'est le bug de production 02:58:12 / 03:00:51.
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
from host_sim import HostSim, host_target_gate

_REF_RECT = {"kind": "img", "id": "6d70afbb-3e36-4204-9f33-21237512c37b"}


def _manifest():
    return {
        "host_name": "hostsim-paint-object",
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
                        "label": {"type": "string"},
                    },
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "target", "required": False}],
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
                        # Type declare par l'hote : chaine, jamais un objet.
                        "target": {"type": "string"},
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


def _host_results():
    def _target(args):
        return {
            "result": True,
            "data": {"target": {
                "ref": dict(_REF_RECT),
                "label": args.get("label") or "outil rectangle",
                "corr": "corr-rect",
                "gen": 1,
            }},
        }

    def _mouse(args):
        refusal = host_target_gate(args.get("target"))
        if refusal is not None:
            return refusal
        return {"result": True, "data": {"action": args.get("action")}}

    return {"target": _target, "mouse": _mouse}


class _Solver:
    def __init__(self, sim):
        self.id = "s-paint-object"
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


async def _run(sim, steps):
    solver = _Solver(sim)
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


@pytest.mark.asyncio
async def test_reference_objet_est_serialisee_pour_le_gate_hote():
    sim = HostSim(results=_host_results())
    await sim.register_manifest(_manifest())

    _solver, results = await _run(sim, [
        _step("step_1", "target", {"cell": "E2", "label": "outil rectangle"}, alias="rect_tool"),
        _step("step_2", "mouse", {"action": "click", "target": "$@_rect_tool"}, expected="any"),
    ])

    assert all(ok for ok, _out, _err in results), [err for ok, _o, err in results if not ok]

    calls = [c for c in sim.host_calls if c["tool_name"] == "mouse"]
    assert len(calls) == 1
    assert calls[0]["arguments"]["target"] == "img:6d70afbb-3e36-4204-9f33-21237512c37b"
    assert not isinstance(calls[0]["arguments"]["target"], dict)
    assert "TARGET_REQUIRED" not in json.dumps(sim.events)


@pytest.mark.asyncio
async def test_gate_hote_refuse_bien_un_objet_si_l_egress_regresse():
    """Garde-fou du banc : un objet envoye tel quel doit etre refuse.

    Si ce test casse, c'est que `host_target_gate` est devenu complaisant et
    que le test precedent ne prouve plus rien. On verifie aussi que la
    membrane egress refuse en amont un objet la ou un nom local est attendu.
    """
    refusal = host_target_gate(dict(_REF_RECT))
    assert refusal["result"] is False
    assert refusal["error_reason"] == "TARGET_REQUIRED"

    sim = HostSim(results={"mouse": lambda args: host_target_gate(args.get("target"))})
    await sim.register_manifest(_manifest())

    upstream = await sim.dispatch("mouse", {"action": "click", "target": dict(_REF_RECT)})
    assert upstream["result"] is False
    assert upstream["error"]["code"] == "TARGET_SCHEMA_INVALID"
    assert upstream["dispatched"] is False
    # L'hote n'a jamais ete appele : la membrane a tranche.
    assert not [c for c in sim.host_calls if c["tool_name"] == "mouse"]

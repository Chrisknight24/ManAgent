"""S6 : surface hote observable (paquets, events, purge, updates)."""

import asyncio
import json
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.constants import Events, SOLVER_MAX_EXECUTION_TRIES
from core.execution_models import FailureClass
from core.plan_models import ExecutionStatus
from core.prompt_capabilities import target_prompt_flags
from core.solver import Solver
from host_sim import HostSim
from tools import internal_tools as IT


def _manifest(*, click_contract=True, scan_contract=True):
    click = {
        "name": "click",
        "kind": "action",
        "description": "Clique une cible deja percue.",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    }
    if click_contract:
        click["target_contract"] = {
            "version": 1,
            "consumes": [{"param": "target", "scope": "ui_element"}],
        }
    scan = {
        "name": "scan",
        "kind": "perception",
        "description": "Scanne les elements visibles.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    }
    if scan_contract:
        scan["target_contract"] = {
            "version": 1,
            "produces": [{
                "path": "data.targets",
                "mode": "candidates",
                "scope": "ui_element",
                "fields": {
                    "ref": "id",
                    "description": "label",
                    "correlation_id": "corr",
                },
            }],
        }
    return {
        "host_name": "hostsim-s6",
        "host_version": "1.0.0",
        "tools": [scan, click],
    }


def _scan(ref="ref-42", label="Fermer", corr="c-42"):
    return {
        "result": True,
        "data": {
            "targets": [{
                "id": ref,
                "label": label,
                "corr": corr,
            }]
        },
    }


async def _host(results=None, *, always_reject=False):
    sim = HostSim(results=results, always_reject=always_reject)
    await sim.register_manifest(_manifest())
    return sim


@pytest.mark.asyncio
async def test_s6_arguments_display_et_dispatched_true():
    sim = await _host({"scan": _scan()})

    scan = await sim.dispatch("scan", {"question": "ou est Fermer ?"})
    local = scan["data"]["targets"][0]["key"]
    assert scan["dispatched"] is True
    assert "ref-42" not in json.dumps(scan, ensure_ascii=False)

    done = await sim.dispatch("click", {"target": local})

    assert done["result"] is True
    assert done["dispatched"] is True
    payload = sim.events_named(Events.TOOL_REQUESTED)[-1]
    assert payload["arguments"] == {"target": "ref-42"}
    assert payload["arguments_display"] == {"target": local}
    assert "ref-42" not in json.dumps(payload["arguments_display"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_s6_refus_enveloppe_dispatched_false_et_aucun_event():
    sim = await _host()

    refused = await sim.dispatch("click", {"target": "target_inexistante"})

    assert refused["result"] is False
    assert refused["data"] is None
    assert refused["dispatched"] is False
    assert refused["error"]["code"] == "TARGET_NOT_FOUND"
    assert refused["message"] == refused["error"]["message"]
    assert sim.host_calls == []
    assert sim.events_named(Events.TOOL_REQUESTED) == []
    assert sim.orchestrator.pending_tool_calls == {}


@pytest.mark.asyncio
async def test_s6_target_updates_appliquees_meme_si_action_en_echec():
    sim = await _host({
        "scan": _scan(),
        "click": {
            "result": False,
            "data": None,
            "message": "Element disparu.",
            "target_updates": [{"correlation_id": "c-42", "op": "invalidate"}],
        },
    })
    scan = await sim.dispatch("scan", {"question": "ou est Fermer ?"})
    local = scan["data"]["targets"][0]["key"]

    done = await sim.dispatch("click", {"target": local})

    assert done["result"] is False
    assert done["dispatched"] is True
    assert "target_updates" not in done
    assert sim.runtime_state.target_registry.find_by_correlation("c-42").active is False


@pytest.mark.asyncio
async def test_s6_manifest_updated_purge_tout_et_eteint_les_flags():
    sim = await _host({"scan": _scan()})
    await sim.dispatch("scan", {"question": "ou est Fermer ?"})
    registry = sim.runtime_state.target_registry
    active = registry.all()[0]
    registry.invalidate(correlation_id="c-42")
    registry.upsert(
        scope="ui_element",
        ref="ref-late",
        description="Autre",
        correlation_id="c-late",
    )
    assert len(registry.all(include_inactive=True)) == 2
    assert target_prompt_flags(sim.runtime_state)["targets_enabled"] is True

    calls = []
    reset = sim.runtime_state.reset_target_registry

    def spy_reset():
        calls.append(True)
        reset()

    sim.runtime_state.reset_target_registry = spy_reset
    await sim.register_manifest(_manifest(click_contract=False, scan_contract=False))

    assert calls == [True]
    assert registry.all(include_inactive=True) == []
    assert registry.find_by_correlation("c-42") is None
    assert registry.find_by_correlation("c-late") is None
    assert active.local_name not in registry.snapshot_view()
    flags = target_prompt_flags(sim.runtime_state)
    assert flags["targets_enabled"] is False
    assert flags["target_selection_enabled"] is False
    manifest_events = sim.events_named(Events.HOST_MANIFEST_UPDATED)
    assert len(manifest_events) == 2
    assert manifest_events[-1]["host_name"] == "hostsim-s6"


@pytest.mark.asyncio
async def test_s6_t19_tout_dispatch_passe_le_chokepoint(monkeypatch):
    async def fake_analysis(data, query, runtime_state, tag="", media_assets=None):
        return {"result": True, "data": {"answer": "ok"}, "error_reason": None}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_analysis)
    sim = await _host({"scan": _scan()})

    chokepoint = []
    original = sim.orchestrator._execute_external_tool

    async def spy(tool_name, arguments):
        chokepoint.append(tool_name)
        return await original(tool_name, arguments)

    sim.orchestrator._execute_external_tool = spy

    scan = await sim.dispatch("scan", {"question": "ou est Fermer ?"})
    local = scan["data"]["targets"][0]["key"]
    await sim.dispatch("click", {"target": local})
    await sim.orchestrator.execute_tool("perceive_understand", {
        "question": "verifie Fermer",
        "source_tool": "scan",
        "source_args": {"question": "ou est Fermer ?"},
    })

    tool_events = sim.events_named(Events.TOOL_REQUESTED)
    assert chokepoint == ["scan", "click", "scan"]
    assert len(sim.host_calls) == len(tool_events) == len(chokepoint)
    assert [call["call_id"] for call in sim.host_calls] == [
        event["call_id"] for event in tool_events
    ]


def _solver_with_host(host, target_name):
    solver = Solver.__new__(Solver)
    solver.id = "s6-host-reject"
    solver.goal = "cliquer sur une cible"
    solver.depth = 0
    solver.context = ""
    solver.parent = SimpleNamespace(id="parent")
    solver.parent_step_id = None
    solver.runtime_state = SimpleNamespace(
        cancel_requested=False,
        solver_registry={},
        cache_manager=None,
        skill_registry=None,
        discovery_engine=None,
        execution_context=SimpleNamespace(scope=lambda **kw: nullcontext()),
        update_marker=lambda k, v: None,
        generation_epoch=0,
    )
    solver.signatures = []
    solver._similar_missions = None
    solver.variable_registry = {}
    solver.current_attempt = None
    solver._preexecution_failures = 0
    solver.last_failure_bundle = None
    solver.last_breakout_report = None
    solver._candidate_skills = []
    calls = {"plans": 0, "execute": 0}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        calls["plans"] += 1
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
        calls["execute"] += 1
        rejected = await host.dispatch("click", {"target": target_name})
        return SimpleNamespace(
            status=ExecutionStatus.FAILED,
            final_context="refus hote",
            error_reason=rejected.get("message") or "refus hote",
            failure_class=FailureClass.EXECUTION_FAILURE,
            target_entity=None,
            failure_bundle=None,
            breakout_report=None,
            material_success_count=0,
            response="",
            execution_tree=None,
        )

    async def fake_validate(*a, **k):
        return True

    async def fake_propagate(*a, **k):
        return None

    async def fake_skill(*a, **k):
        return None

    async def no_extension(*a, **k):
        return False

    solver._check_feasibility = fake_feasibility
    solver.planner = SimpleNamespace(propose_plan=fake_plan, _cached_advice=None)
    solver.executor = SimpleNamespace(execute_plan=fake_execute)
    solver.validate_plan = fake_validate
    solver.propagate_event = fake_propagate
    solver._handle_skill_lifecycle_post_execution = fake_skill
    solver._maybe_extend_budget = no_extension
    solver._get_registry_metadata_view = lambda: {}
    solver._format_registry_view = lambda meta: ""
    solver.calls = calls
    return solver


def test_s6_t17_hote_qui_rejette_toujours_termine():
    async def scenario():
        sim = await _host(always_reject=True)
        entry = sim.runtime_state.target_registry.upsert(
            scope="ui_element",
            ref="ref-host-reject",
            description="Cible refusee",
            correlation_id="c-reject",
        )
        solver = _solver_with_host(sim, entry.local_name)
        result = await solver.run()
        return sim, solver, result

    sim, solver, result = asyncio.run(scenario())

    assert result.status == ExecutionStatus.FAILED
    assert 1 <= solver.calls["execute"] <= SOLVER_MAX_EXECUTION_TRIES
    assert len(sim.host_calls) == solver.calls["execute"]
    assert all(call["result"]["result"] is False for call in sim.host_calls)

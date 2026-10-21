"""S1 : membrane egress (noms locaux -> references hote, avant dispatch)."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.orchestrator import Orchestrator
from core.target_contract import ConsumerSpec, TargetContract
from core.targets import TargetRegistry
from tools.internal_tools import _prevalidate_external
from tools.tools_manager import ToolsManager


def _contract(*params, scope="ui_element", raw=None):
    consumes = []
    for param in params:
        entry = {"param": param, "scope": scope}
        if raw is not None:
            entry["raw"] = raw
        consumes.append(entry)
    return {"version": 1, "consumes": consumes}


def _schema(*params):
    return {
        "type": "object",
        "properties": {param: {"type": "string"} for param in params},
        "required": list(params),
    }


def _manager(tool_name="click", params=("target",), contract=None):
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name=tool_name,
        role="action",
        description="action",
        parameters_schema=_schema(*params),
        target_contract=contract if contract is not None else _contract(*params),
    )
    return tm


def _runtime(tm):
    return SimpleNamespace(
        target_registry=TargetRegistry(),
        tools_manager=tm,
    )


class _FakeOrchestrator:
    def __init__(self, runtime_state, host_result=None):
        self.runtime_state = runtime_state
        self.pending_tool_calls = {}
        self.events = []
        self.host_result = host_result or json.dumps({"result": True, "data": {"ok": 1}})

    _resolve_target_arguments = Orchestrator._resolve_target_arguments
    _mark_dispatched = staticmethod(Orchestrator._mark_dispatched)

    async def propagate_event(self, event_name, payload):
        self.events.append((event_name, payload))
        future = self.pending_tool_calls.get(payload.get("call_id"))
        if future is not None and not future.done():
            future.set_result(self.host_result)


class _PurgingOrchestrator(_FakeOrchestrator):
    async def propagate_event(self, event_name, payload):
        self.runtime_state.target_registry.purge(all=True)
        await super().propagate_event(event_name, payload)


def _register(runtime_state, ref=None, scope="ui_element", description="Bouton"):
    return runtime_state.target_registry.upsert(
        scope=scope,
        ref=ref if ref is not None else {"id": 7},
        description=description,
        correlation_id="corr-1",
    )


@pytest.mark.asyncio
async def test_consumer_name_is_substituted_then_resolved_to_raw_ref():
    tm = _manager()
    runtime_state = _runtime(tm)
    entry = _register(runtime_state)
    executor = Executor(SimpleNamespace(
        variable_registry={"cible": {"value": entry.local_name, "type": "text"}},
        runtime_state=runtime_state,
    ))

    args = executor._substitute_consumer_values(
        {"target": "$@_cible"}, {"target"}
    )
    assert args == {"target": entry.local_name}

    orchestrator = _FakeOrchestrator(runtime_state)
    raw = await Orchestrator._execute_external_tool(orchestrator, "click", args)
    parsed = json.loads(raw)
    assert parsed["result"] is True
    assert parsed["dispatched"] is True

    assert len(orchestrator.events) == 1
    _event, payload = orchestrator.events[0]
    assert payload["arguments"] == {"target": {"id": 7}}
    assert payload["arguments_display"] == {"target": entry.local_name}
    assert entry.local_name not in json.dumps(payload["arguments"])


@pytest.mark.asyncio
async def test_unknown_consumer_value_is_refused_without_dispatch():
    tm = _manager()
    runtime_state = _runtime(tm)
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator, "click", {"target": "inconnu"}
    )
    parsed = json.loads(raw)
    assert parsed["result"] is False
    assert parsed["dispatched"] is False
    assert parsed["error"]["code"] == "TARGET_NOT_FOUND"
    assert orchestrator.events == []


@pytest.mark.asyncio
async def test_raw_allow_is_an_explicit_passthrough():
    tm = _manager("legacy", contract=_contract("target", raw="allow"))
    runtime_state = _runtime(tm)
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator, "legacy", {"target": "btn-42"}
    )
    parsed = json.loads(raw)
    assert parsed["result"] is True
    assert orchestrator.events[0][1]["arguments"] == {"target": "btn-42"}
    assert "arguments_display" not in orchestrator.events[0][1]


@pytest.mark.asyncio
async def test_multi_target_is_all_or_nothing():
    tm = _manager("drag", params=("src", "dst"))
    runtime_state = _runtime(tm)
    entry = _register(runtime_state, ref={"id": 1})
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator,
        "drag",
        {"src": entry.local_name, "dst": "absent"},
    )
    parsed = json.loads(raw)
    assert parsed["result"] is False
    assert parsed["dispatched"] is False
    assert parsed["error"]["code"] == "TARGET_NOT_FOUND"
    assert orchestrator.events == []


@pytest.mark.asyncio
async def test_inactive_target_is_refused_without_dispatch():
    tm = _manager()
    runtime_state = _runtime(tm)
    entry = _register(runtime_state)
    runtime_state.target_registry.invalidate(correlation_id="corr-1")
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator, "click", {"target": entry.local_name}
    )
    parsed = json.loads(raw)
    assert parsed["result"] is False
    assert parsed["dispatched"] is False
    assert parsed["error"]["code"] == "TARGET_INACTIVE"
    assert orchestrator.events == []


@pytest.mark.asyncio
async def test_scope_mismatch_is_refused_without_dispatch():
    tm = _manager(contract=_contract("target", scope="window"))
    runtime_state = _runtime(tm)
    entry = _register(runtime_state, scope="ui_element")
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator, "click", {"target": entry.local_name}
    )
    parsed = json.loads(raw)
    assert parsed["result"] is False
    assert parsed["dispatched"] is False
    assert parsed["error"]["code"] == "TARGET_SCOPE_MISMATCH"
    assert orchestrator.events == []


@pytest.mark.asyncio
async def test_mixed_text_and_target_is_rejected_not_trimmed():
    tm = _manager()
    runtime_state = _runtime(tm)
    entry = _register(runtime_state)
    executor = Executor(SimpleNamespace(
        variable_registry={"cible": {"value": entry.local_name, "type": "text"}},
        runtime_state=runtime_state,
    ))
    args = executor._substitute_consumer_values(
        {"target": f"clique {entry.local_name}"}, {"target"}
    )
    orchestrator = _FakeOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(orchestrator, "click", args)
    parsed = json.loads(raw)
    assert parsed["result"] is False
    assert parsed["dispatched"] is False
    assert parsed["error"]["code"] == "TARGET_NOT_FOUND"
    assert orchestrator.events == []

    unresolved = json.loads(await Orchestrator._execute_external_tool(
        orchestrator, "click", {"target": "$@_cible"}
    ))
    assert unresolved["error"]["code"] == "TARGET_NOT_FOUND"
    assert orchestrator.events == []


@pytest.mark.asyncio
async def test_purge_during_in_flight_call_keeps_resolved_snapshot():
    tm = _manager()
    runtime_state = _runtime(tm)
    entry = _register(runtime_state)
    orchestrator = _PurgingOrchestrator(runtime_state)

    raw = await Orchestrator._execute_external_tool(
        orchestrator, "click", {"target": entry.local_name}
    )
    parsed = json.loads(raw)
    assert parsed["result"] is True
    assert parsed["dispatched"] is True
    assert orchestrator.events[0][1]["arguments"] == {"target": {"id": 7}}
    assert len(runtime_state.target_registry) == 0


@pytest.mark.asyncio
async def test_tool_without_contract_keeps_host_result_untouched():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="plain",
        role="action",
        description="action",
        parameters_schema={"type": "object", "properties": {}},
    )
    runtime_state = _runtime(tm)
    host_result = json.dumps({"result": True, "data": {"ok": 1}})
    orchestrator = _FakeOrchestrator(runtime_state, host_result=host_result)

    raw = await Orchestrator._execute_external_tool(orchestrator, "plain", {})
    assert raw == host_result
    assert "dispatched" not in json.loads(raw)


def test_enum_check_exempts_declared_consumer_params():
    schema = {
        "type": "object",
        "properties": {"target": {"type": "string", "enum": ["a", "b"]}},
        "required": ["target"],
    }
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="click",
        role="action",
        description="action",
        parameters_schema=schema,
    )
    tm._target_contracts["click"] = TargetContract(
        version=1,
        consumers=(ConsumerSpec(param="target", scope="ui_element"),),
        tool_name="click",
    )

    assert _prevalidate_external(tm, "click", {"target": "nom_local"}) is None

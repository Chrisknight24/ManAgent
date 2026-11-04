"""Liaison runtime des cibles produites directement par l'hote.

Couvre le seam qui manquait : un producteur direct (`target`) doit publier
son `output_target_name` dans le registre de variables, afin que le
consommateur (`mouse`) substitue `$@_alias` en nom local puis laisse la
membrane egress resoudre ce nom en reference brute.
"""

import asyncio
import json
import types

from core.executor import Executor
from core.orchestrator import Orchestrator
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_unresolved_plan_refs
from core.target_contract import resolve_consumer_arguments
from core.targets import TargetRegistry
from tools.tools_manager import ToolsManager


def _schema(*params):
    return {
        "type": "object",
        "properties": {param: {"type": "string"} for param in params},
        "required": list(params),
    }


def _producer_contract():
    return {
        "version": 1,
        "produces": [{
            "path": "data.target",
            "mode": "single",
            "scope": "ui_element",
            "fields": {
                "ref": "ref",
                "description": "label",
                "correlation_id": "corr",
                "generation": "gen",
            },
        }],
    }


def _consumer_contract():
    return {
        "version": 1,
        "consumes": [{"param": "target", "scope": "ui_element"}],
    }


def _manager():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="target",
        role="action",
        description="target",
        parameters_schema=_schema("cell", "label"),
        target_contract=_producer_contract(),
    )
    tm.register_tool(
        name="mouse",
        role="action",
        description="mouse",
        parameters_schema=_schema("action", "target"),
        target_contract=_consumer_contract(),
    )
    return tm


def _runtime(tm):
    return types.SimpleNamespace(
        session_id="sess-binding",
        discovery_engine=None,
        target_registry=TargetRegistry(),
        tools_manager=tm,
    )


def _solver(runtime_state, result_payload):
    async def _fake_exec(name, args):
        return json.dumps(result_payload)

    return types.SimpleNamespace(
        id="s-binding",
        variable_registry={},
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=runtime_state,
        execute_tool=_fake_exec,
    )


def _target_step(alias="pt_start"):
    return PlanStep(
        id="step_2",
        description="target",
        type=StepType.TOOL_CALL,
        tool_name="target",
        tool_args_json=json.dumps({"cell": "E4", "label": alias}),
        expected_result="true",
        output_target_name=alias,
    )


def _host_target_payload():
    return {
        "result": True,
        "data": {"target": {
            "ref": "raw-ref-e4",
            "label": "pt_start",
            "corr": "corr-e4",
            "gen": 1,
        }},
    }


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


def _run_target(solver):
    ex = Executor.__new__(Executor)
    ex.solver = solver
    node = types.SimpleNamespace(metadata={})
    return asyncio.run(ex._handle_tool_call(_target_step(), node=node))


def test_target_producer_publishes_alias_to_local_name():
    tm = _manager()
    runtime_state = _runtime(tm)
    solver = _solver(runtime_state, _host_target_payload())

    ok, output, error = _run_target(solver)

    assert ok is True
    assert error is None
    entry = runtime_state.target_registry.find_by_ref("raw-ref-e4")
    assert entry is not None and entry.active is True
    binding = solver.variable_registry["pt_start"]
    assert binding["value"] == entry.local_name
    assert binding["type"] == "target"
    assert binding["kind"] == "target"
    assert "raw-ref-e4" not in json.dumps(binding)


def test_target_alias_substitutes_then_dispatches_raw_ref_to_mouse():
    tm = _manager()
    runtime_state = _runtime(tm)
    solver = _solver(runtime_state, _host_target_payload())
    ok, _, _ = _run_target(solver)
    assert ok is True

    entry = runtime_state.target_registry.find_by_ref("raw-ref-e4")
    assert entry is not None

    # Appel via une instance minimale : la methode ne depend que du solver.
    ex = Executor.__new__(Executor)
    ex.solver = solver
    args = ex._substitute_consumer_values(
        {"action": "press", "target": "$@_pt_start"}, {"target"}
    )
    assert args == {"action": "press", "target": entry.local_name}

    orchestrator = _FakeOrchestrator(runtime_state)
    raw = asyncio.run(
        Orchestrator._execute_external_tool(orchestrator, "mouse", args)
    )
    parsed = json.loads(raw)
    assert parsed["result"] is True
    assert parsed["dispatched"] is True
    assert len(orchestrator.events) == 1

    _event, payload = orchestrator.events[0]
    assert payload["arguments"] == {"action": "press", "target": "raw-ref-e4"}
    assert payload["arguments_display"] == {
        "action": "press",
        "target": entry.local_name,
    }
    assert "$@_" not in json.dumps(payload["arguments"])


def test_target_producer_without_projected_entry_fails_closed():
    tm = _manager()
    runtime_state = _runtime(tm)
    solver = _solver(runtime_state, {"result": True, "data": {"other": 1}})

    ok, _, error = _run_target(solver)

    assert ok is False
    assert "Cible non liée" in (error or "")


def test_predispatch_target_refusal_exposes_code_message_and_dispatched():
    tm = _manager()
    runtime_state = _runtime(tm)
    refusal = {
        "result": False,
        "data": None,
        "dispatched": False,
        "error": {
            "code": "TARGET_NOT_FOUND",
            "message": "unresolved target reference '$@_missing' for consumer 'target'",
        },
        "message": "unresolved target reference '$@_missing' for consumer 'target'",
    }
    solver = _solver(runtime_state, refusal)
    ex = Executor.__new__(Executor)
    ex.solver = solver
    step = PlanStep(
        id="step_4",
        description="mouse",
        type=StepType.TOOL_CALL,
        tool_name="mouse",
        tool_args_json=json.dumps({"action": "press", "target": "$@_missing"}),
        expected_result="any",
    )

    ok, output, error = asyncio.run(ex._handle_tool_call(step))

    assert ok is True  # le step est materialise, la convergence tranchera
    assert "[pre-dispatch]" in (error or "")
    assert "TARGET_NOT_FOUND" in (error or "")
    assert "unresolved target reference" in (error or "")
    assert "Rejet matériel" not in (error or "")


def test_pure_unresolved_token_is_not_reported_as_mixed_text():
    tm = _manager()
    runtime_state = _runtime(tm)
    contract = tm.get_target_contract("mouse")

    _, _, error = resolve_consumer_arguments(
        runtime_state.target_registry, contract, {"target": "$@_missing"}
    )
    assert error is not None
    assert error["code"] == "TARGET_NOT_FOUND"
    assert "unresolved target reference" in error["message"]
    assert "mixes text" not in error["message"]

    _, _, mixed = resolve_consumer_arguments(
        runtime_state.target_registry, contract, {"target": "click $@_missing"}
    )
    assert mixed is not None
    assert "mixes text and target reference" in mixed["message"]


def test_unknown_target_alias_is_rejected_by_deterministic_gate():
    producer = PlanStep(
        id="step_2",
        description="target",
        type=StepType.TOOL_CALL,
        tool_name="target",
        tool_args_json=json.dumps({"cell": "E4", "label": "pt_start"}),
        expected_result="true",
        output_target_name="pt_start",
    )
    consumer = PlanStep(
        id="step_4",
        description="mouse",
        type=StepType.TOOL_CALL,
        tool_name="mouse",
        tool_args_json=json.dumps({"action": "press", "target": "$@_unknown"}),
        expected_result="any",
    )

    hits = find_unresolved_plan_refs(Plan(goal="g", steps=[producer, consumer]))

    assert hits
    assert any("step_4" in hit and "$@_unknown" in hit for hit in hits)

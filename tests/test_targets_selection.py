"""S3 : selection de cibles dans les meta-outils perceive_*."""

import asyncio
import json
import types

from core.executor import Executor
from core.plan_models import PlanStep, StepType
from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload, selection_from_payload
from core.targets import TargetRegistry
from tools import internal_tools as IT


def _contract(*, mode="candidates", fields=None, tool_name="scan"):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.targets",
            "mode": mode,
            "scope": "ui_element",
            "fields": fields or {
                "ref": "id",
                "description": "label",
                "correlation_id": "correlation_id",
            },
        }],
    }
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


class _FakeManager:
    def __init__(self, contracts, payloads):
        self.contracts = contracts
        self.payloads = payloads
        self.calls = []

    def get_target_contract(self, name):
        return self.contracts.get(name)

    def get_tool_returns(self, name):
        return []

    async def execute_tool(self, name, args):
        self.calls.append((name, dict(args)))
        return json.dumps(self.payloads[name])


def _runtime(manager, registry, variables=None):
    return types.SimpleNamespace(
        tools_manager=manager,
        target_registry=registry,
        _solver_registry_for_tools=variables if variables is not None else {},
        language="en",
    )


def _scan_payload(items):
    return {"result": True, "data": {"targets": items}}


def _single_payload(item):
    return {"result": True, "data": {"targets": item}}


def test_t9_selection_none_single_candidates_et_cles_mintées():
    contract = _contract()
    registry = TargetRegistry()

    empty = project_target_payload(registry, contract, _scan_payload([]))[0]
    assert selection_from_payload(registry, contract, empty).kind == "none"

    one = project_target_payload(registry, contract, _scan_payload(
        [{"id": "ref-one", "label": "Un"}]))[0]
    sel_one = selection_from_payload(registry, contract, one)
    assert sel_one.kind == "single"
    assert len(sel_one.entries) == 1

    many = project_target_payload(registry, contract, _scan_payload([
        {"id": "ref-a", "label": "A"},
        {"id": "ref-b", "label": "B"},
        {"id": "ref-c", "label": "C"},
    ]))[0]
    sel_many = selection_from_payload(registry, contract, many)
    assert sel_many.kind == "candidates"
    assert len({e.local_name for e in sel_many.entries}) == 3
    assert "ref-a" not in json.dumps(many)


def test_t10_single_auto_selection_expose_description():
    contract = _contract(mode="single")
    registry = TargetRegistry()
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _single_payload({"id": "ref-ok", "label": "Bouton OK"}),
         "click": {"result": True, "data": {"clicked": True}}},
    )
    runtime = _runtime(manager, registry)

    out = asyncio.run(IT.perceive_action({
        "question": "clique sur OK",
        "source_tool": "scan",
        "action_tool": "click",
        "action_args": {"cell": "$TARGET"},
    }, runtime))

    assert out["result"] is True
    assert manager.calls == [
        ("scan", {}),
        ("click", {"cell": "ref-ok"}),
    ]
    assert out["data"]["target"]["description"] == "Bouton OK"
    assert out["data"]["target"]["key"] in registry
    assert "ref-ok" not in json.dumps(out["data"]["seen"])
    assert "ref-ok" not in json.dumps(out["data"]["target"])


def test_t11_output_target_name_lie_un_nom_reutilisable(monkeypatch):
    contract = _contract(mode="single")
    registry = TargetRegistry()
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _single_payload({"id": "ref-close", "label": "Fermer"})},
    )
    variables = {}
    runtime = _runtime(manager, registry, variables)
    seen = {}

    async def fake_analysis(data, query, runtime_state, tag="", media_assets=None):
        seen["data"] = data
        seen["query"] = query
        return {"result": True, "data": {"answer": "ok"}, "error_reason": None}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_analysis)

    out = asyncio.run(IT.perceive_understand({
        "question": "trouve fermer",
        "source_tool": "scan",
        "_output_target_name": "bouton_fermer",
    }, runtime))

    entry = registry.find_by_ref("ref-close")
    assert out["result"] is True
    assert out["target"]["key"] == entry.local_name
    assert out["target_name"] == "bouton_fermer"
    bound = variables["bouton_fermer"]
    assert bound["value"] == entry.local_name
    assert bound["type"] == "target" and bound["kind"] == "target"
    assert registry.resolve(bound["value"]).value == "ref-close"
    assert "ref-close" not in json.dumps(variables)
    assert "ref-close" not in json.dumps(seen["data"])


def test_t12_perceive_action_selectionne_sur_donnees_projetees(monkeypatch):
    contract = _contract()
    registry = TargetRegistry()
    first = registry.upsert(scope="ui_element", ref="ref-a",
                            description="Premier", correlation_id="c-a")
    second = registry.upsert(scope="ui_element", ref="ref-b",
                             description="Deuxieme", correlation_id="c-b")
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _scan_payload([
            {"id": "ref-a", "label": "Premier", "correlation_id": "c-a"},
            {"id": "ref-b", "label": "Deuxieme", "correlation_id": "c-b"},
        ]), "click": {"result": True, "data": {"clicked": True}}},
    )
    runtime = _runtime(manager, registry)
    prompts = []

    class FakeLlm:
        async def generate_structured(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return types.SimpleNamespace(target_key=second.local_name, reason="ok")

    async def fake_get_llm(runtime_state):
        return FakeLlm()

    monkeypatch.setattr(IT, "_get_tools_llm", fake_get_llm)

    out = asyncio.run(IT.perceive_action({
        "question": "clique sur Deuxieme",
        "source_tool": "scan",
        "action_tool": "click",
        "target_hint": "Deuxieme",
        "action_args": {"cell": "$TARGET"},
    }, runtime))

    assert out["result"] is True
    assert first.local_name in prompts[0] and second.local_name in prompts[0]
    assert "ref-a" not in prompts[0] and "ref-b" not in prompts[0]
    assert out["data"]["target"]["key"] == second.local_name
    assert manager.calls[-1] == ("click", {"cell": "ref-b"})
    assert "ref-b" not in json.dumps(out["data"]["seen"])


def test_t16_cle_candidate_inconnue_rejetee_avant_action(monkeypatch):
    contract = _contract()
    registry = TargetRegistry()
    registry.upsert(scope="ui_element", ref="ref-a",
                    description="A", correlation_id="c-a")
    registry.upsert(scope="ui_element", ref="ref-b",
                    description="B", correlation_id="c-b")
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _scan_payload([
            {"id": "ref-a", "label": "A", "correlation_id": "c-a"},
            {"id": "ref-b", "label": "B", "correlation_id": "c-b"},
        ]), "click": {"result": True}},
    )
    runtime = _runtime(manager, registry)

    class FakeLlm:
        async def generate_structured(self, **kwargs):
            return types.SimpleNamespace(target_key="ghost", reason="inventee")

    async def fake_get_llm(runtime_state):
        return FakeLlm()

    monkeypatch.setattr(IT, "_get_tools_llm", fake_get_llm)

    out = asyncio.run(IT.perceive_action({
        "question": "clique",
        "source_tool": "scan",
        "action_tool": "click",
        "action_args": {"cell": "$TARGET"},
    }, runtime))

    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_NOT_FOUND"
    assert [name for name, _ in manager.calls] == ["scan"]


def test_t23_aba_selectionne_un_nouveau_nom_jamais_l_ancien():
    contract = _contract(mode="single")
    registry = TargetRegistry()
    old = registry.upsert(scope="ui_element", ref="ref-aba",
                          description="Ancienne", correlation_id="c-old")
    assert registry.invalidate(correlation_id="c-old") == 1
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _single_payload({
            "id": "ref-aba", "label": "Nouvelle", "correlation_id": "c-new",
        }), "click": {"result": True, "data": {"clicked": True}}},
    )
    runtime = _runtime(manager, registry)

    out = asyncio.run(IT.perceive_action({
        "question": "clique",
        "source_tool": "scan",
        "action_tool": "click",
        "action_args": {"cell": "$TARGET"},
    }, runtime))

    new = registry.find_by_correlation("c-new")
    assert out["result"] is True
    assert new is not None and new.local_name != old.local_name
    assert registry.get(old.local_name).active is False
    assert manager.calls[-1] == ("click", {"cell": "ref-aba"})
def test_t11_executor_transmet_output_target_name_au_meta_outil(monkeypatch):
    contract = _contract(mode="single")
    registry = TargetRegistry()
    manager = _FakeManager(
        {"scan": contract},
        {"scan": _single_payload({"id": "ref-exec", "label": "Executer"})},
    )
    variables = {}
    runtime = _runtime(manager, registry, variables)

    async def fake_analysis(data, query, runtime_state, tag="", media_assets=None):
        return {"result": True, "data": {"answer": "ok"}, "error_reason": None}

    async def fake_exec(name, args):
        return json.dumps(await IT.perceive_understand(args, runtime))

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_analysis)

    solver = types.SimpleNamespace(
        id="s-sel",
        variable_registry=variables,
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=runtime,
        execute_tool=fake_exec,
    )
    ex = Executor.__new__(Executor)
    ex.solver = solver
    step = PlanStep(
        id="step_1", description="percoit", type=StepType.TOOL_CALL,
        tool_name="perceive_understand",
        tool_args_json=json.dumps({"question": "trouve", "source_tool": "scan"}),
        expected_result="true", output_target_name="cible_exec",
    )
    node = types.SimpleNamespace(metadata={})

    ok, output, _ = asyncio.run(ex._handle_tool_call(step, node=node))

    entry = registry.find_by_ref("ref-exec")
    assert ok is True
    assert variables["cible_exec"]["value"] == entry.local_name
    assert variables["cible_exec"]["type"] == "target"
    for payload in (node.result, variables, output):
        assert "ref-exec" not in json.dumps(payload)

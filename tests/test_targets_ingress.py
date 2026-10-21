"""S2 : projection ingress des references cibles avant stockage."""

import asyncio
import json
import types

from core.executor import Executor
from core.plan_models import PlanStep, StepType
from core.target_contract import parse_target_contract
from core.target_ingress import (
    apply_target_updates,
    project_target_payload,
    strip_target_updates,
)
from core.targets import TargetRegistry


def _contract(*, path="data.targets", mode="candidates", scope="ui_element",
              fields=None, tool_name="scan"):
    raw = {
        "version": 1,
        "produces": [{
            "path": path,
            "mode": mode,
            "scope": scope,
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


def _payload(items, corr=None):
    items = [dict(item) for item in items]
    if corr is not None:
        for item in items:
            item.setdefault("correlation_id", corr)
    return {"result": True, "data": {"targets": items}}


def test_t15_projection_renomme_les_champs_et_la_ref():
    contract = _contract(fields={
        "ref": "element.handle",
        "description": "caption",
        "correlation_id": "meta.cid",
        "generation": "meta.gen",
    }, scope="ui_element")
    registry = TargetRegistry()

    projected, count = project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "element": {"handle": "raw-ref-75"},
            "caption": "Bouton fermer",
            "meta": {"cid": "c-75", "gen": 2},
        }]},
    })

    entry = registry.find_by_ref("raw-ref-75")
    assert count == 1 and entry is not None
    assert entry.ref.value == "raw-ref-75"
    target = projected["data"]["targets"][0]
    assert target == {
        "key": entry.local_name,
        "description": "Bouton fermer",
        "scope": "ui_element",
    }
    assert "raw-ref-75" not in json.dumps(projected)


def test_t21_dedup_par_correlation_puis_par_ref():
    contract = _contract()
    registry = TargetRegistry()

    first, _ = project_target_payload(registry, contract, _payload(
        [{"id": "ref-1", "label": "Premier"}], corr="c-1"))
    name = first["data"]["targets"][0]["key"]

    second, _ = project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{"id": "ref-2", "label": "Deuxieme",
                              "correlation_id": "c-1"}]},
    })
    third, _ = project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{"id": "ref-2", "label": "Deuxieme"}]},
    })

    assert len(registry.all()) == 1
    assert registry.get(name).ref.value == "ref-2"
    assert second["data"]["targets"][0]["key"] == name
    assert third["data"]["targets"][0]["key"] == name


def test_t22_generation_absente_ou_ancienne_n_ecrase_pas():
    contract = _contract(fields={
        "ref": "id",
        "description": "label",
        "correlation_id": "correlation_id",
        "generation": "generation",
    })
    registry = TargetRegistry()

    project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "id": "ref-new", "label": "Nouveau",
            "correlation_id": "c-gen", "generation": 2,
        }]},
    })
    project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "id": "ref-old", "label": "Ancien",
            "correlation_id": "c-gen", "generation": 1,
        }]},
    })
    project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "id": "ref-old", "label": "Ancien",
            "correlation_id": "c-gen",
        }]},
    })

    entry = registry.find_by_correlation("c-gen")
    assert entry is not None
    assert entry.ref.value == "ref-new"
    assert entry.generation == 2


def test_t23_aba_ne_reutilise_pas_un_nom_invalide():
    contract = _contract(fields={
        "ref": "id",
        "description": "label",
        "correlation_id": "correlation_id",
    })
    registry = TargetRegistry()

    project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "id": "ref-aba", "label": "Cible", "correlation_id": "c-old",
        }]},
    })
    old = registry.find_by_correlation("c-old")
    assert old is not None

    assert registry.invalidate(correlation_id="c-old") == 1
    project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [{
            "id": "ref-aba", "label": "Cible", "correlation_id": "c-new",
        }]},
    })
    new = registry.find_by_correlation("c-new")

    assert new is not None and new.local_name != old.local_name
    assert registry.get(old.local_name).active is False


def test_t25_snapshot_projette_sans_inscrire_ni_fuite():
    contract = _contract()
    registry = TargetRegistry()
    existing = registry.upsert(
        scope="ui_element", ref="ref-known", description="Connue",
        correlation_id="c-known",
    )

    projected, count = project_target_payload(registry, contract, {
        "result": True,
        "data": {"targets": [
            {"id": "ref-known", "label": "Connue"},
            {"id": "ref-unknown", "label": "Inconnue"},
        ]},
        "target_updates": [{"correlation_id": "c-known"}],
    }, register=False)
    projected = strip_target_updates(projected)

    assert count == 1
    assert len(registry.all()) == 1
    assert registry.find_by_ref("ref-known").active is True
    assert projected["data"]["targets"] == [{
        "key": existing.local_name,
        "description": "Connue",
        "scope": "ui_element",
    }]
    assert "ref-unknown" not in json.dumps(projected)
    assert "target_updates" not in projected


def test_t26_updates_appliquees_puis_retirees_du_payload():
    registry = TargetRegistry()
    registry.upsert(scope="ui_element", ref="ref-gone",
                    description="Partante", correlation_id="c-gone")

    projected, applied = apply_target_updates(registry, {
        "result": True,
        "data": {"ok": True},
        "target_updates": [{"correlation_id": "c-gone", "op": "invalidate"}],
    })

    assert applied == 1
    assert registry.find_by_correlation("c-gone").active is False
    assert "target_updates" not in projected
    assert projected["data"] == {"ok": True}


class _FakeToolsManager:
    def __init__(self, contract):
        self.contract = contract

    def get_target_contract(self, name):
        return self.contract if name == "scan" else None

    def get_tool_returns(self, name):
        return []


def _executor(result, contract, registry):
    async def _fake_exec(name, args):
        return json.dumps(result)

    solver = types.SimpleNamespace(
        id="s-ingress",
        variable_registry={},
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=types.SimpleNamespace(
            session_id="sess",
            discovery_engine=None,
            target_registry=registry,
            tools_manager=_FakeToolsManager(contract),
        ),
        execute_tool=_fake_exec,
    )
    ex = Executor.__new__(Executor)
    ex.solver = solver
    return ex, solver


def _step():
    return PlanStep(id="step_1", description="scan", type=StepType.TOOL_CALL,
                    tool_name="scan", tool_args_json="{}",
                    expected_result="true")


def test_t18_executor_ne_stocke_ni_ne_rend_la_ref_brute():
    contract = _contract()
    registry = TargetRegistry()
    ex, solver = _executor({
        "result": True,
        "data": {"targets": [{
            "id": "ref-canary-42", "label": "Fermer",
        }]},
    }, contract, registry)
    node = types.SimpleNamespace(metadata={})

    ok, output, _ = asyncio.run(ex._handle_tool_call(_step(), node=node))

    entry = registry.find_by_ref("ref-canary-42")
    assert ok is True and entry is not None
    stored = solver.variable_registry["data_step_1"]["value"]
    assert stored["targets"] == [{
        "key": entry.local_name,
        "description": "Fermer",
        "scope": "ui_element",
    }]
    for payload in (stored, node.result, output):
        assert "ref-canary-42" not in json.dumps(payload)


def test_t26_executor_applique_update_meme_sur_echec_et_la_retire():
    contract = _contract()
    registry = TargetRegistry()
    registry.upsert(scope="ui_element", ref="ref-gone",
                    description="Partante", correlation_id="c-gone")
    ex, solver = _executor({
        "result": False,
        "data": None,
        "target_updates": [{"correlation_id": "c-gone", "op": "invalidate"}],
    }, contract, registry)
    node = types.SimpleNamespace(metadata={})

    asyncio.run(ex._handle_tool_call(_step(), node=node))

    assert registry.find_by_correlation("c-gone").active is False
    assert "target_updates" not in node.result
    assert "target_updates" not in json.dumps(
        solver.variable_registry["data_step_1"]["value"])

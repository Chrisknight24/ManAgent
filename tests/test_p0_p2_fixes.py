"""Correctifs P0-P2 issus de l'analyse de la mission YouTube (2026-11-06).

Chaque test couvre un comportement agnostique de l'hôte :
- P1-6 : type réel + provenance (tentative) des alias de cible ;
- P0-3 : élargissement borné de l'analyse quand le crop échoue ;
- P2-10 : résolution déterministe d'une correspondance exacte unique ;
- P1-8 : événements de tentative distinguant plan proposé / plan exécuté ;
- P1-5 / P2-11 : promesses des prompts (pas de substitution, pas de `\\n`).
"""

import asyncio
import types

import core.solver as solver_module
from core.executor import Executor
from core.prompt_loader import registry_compact
from core.solver import Solver
from core.target_contract import declared_target_kind, parse_target_contract
from core.target_ingress import bind_target_alias
from core.targeting import ObservedCandidate, ObservedWorld, exact_candidate
from core.targets import TargetRegistry
from tools import internal_tools as IT


def _contract(kind_param="kind"):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.target",
            "mode": "single",
            "scope": "ui_element",
            "kind_param": kind_param,
            "fields": {
                "ref": "id",
                "description": "label",
            },
        }],
    }
    result = parse_target_contract(raw, tool_name="target")
    assert result.contract is not None, result.errors
    return result.contract


def _solver_with_targets(registry, variables, attempt_no=2):
    solver = Solver.__new__(Solver)
    solver.runtime_state = types.SimpleNamespace(target_registry=registry)
    solver.variable_registry = variables
    solver.current_attempt = types.SimpleNamespace(attempt_number=attempt_no)
    return solver


def test_declared_target_kind_uses_contract_parameter():
    contract = _contract(kind_param="target_kind")
    assert declared_target_kind(contract, {"target_kind": "ui"}) == "ui"
    assert declared_target_kind(contract, {"kind": "ui"}) == ""
    assert declared_target_kind(None, {"kind": "ui"}) == ""


def test_bind_target_alias_stores_kind_and_attempt():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui_preset", ref="ref-1", description="Barre")
    variables = {}

    error = bind_target_alias(
        registry, variables, "barre", entry,
        target_kind="ui", attempt_no=3,
    )

    assert error is None
    stored = variables["barre"]
    assert stored["value"] == entry.local_name
    assert stored["type"] == "target"
    assert stored["target_kind"] == "ui"
    assert stored["attempt_no"] == 3


def test_bind_target_alias_falls_back_to_scope():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui_preset", ref="ref-1", description="Barre")
    variables = {}

    assert bind_target_alias(registry, variables, "barre", entry) is None
    assert variables["barre"]["target_kind"] == "ui_preset"


def test_registry_view_marks_previous_attempt_as_not_declarable():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui_preset", ref="ref-1", description="Barre")
    solver = _solver_with_targets(
        registry,
        {
            "barre": {
                "value": entry.local_name,
                "type": "target",
                "target_kind": "ui_preset",
                "attempt_no": 1,
                "description": "Barre",
            }
        },
        attempt_no=2,
    )

    view = solver._get_registry_metadata_view()
    meta = view["barre"]

    assert meta["target_kind"] == "ui_preset"
    assert meta["declarable"] is False
    assert "tentative 1" in meta["stale_reason"]

    compact = registry_compact(view, prefix="$@_")
    assert "(target ui_preset)" in compact
    assert "non déclarable dans ce plan" in compact


def test_registry_view_keeps_current_attempt_declarable():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui_preset", ref="ref-1", description="Barre")
    solver = _solver_with_targets(
        registry,
        {
            "barre": {
                "value": entry.local_name,
                "type": "target",
                "attempt_no": 2,
                "description": "Barre",
            }
        },
        attempt_no=2,
    )

    meta = solver._get_registry_metadata_view()["barre"]
    assert meta["declarable"] is True
    assert "stale_reason" not in meta


def test_failed_step_does_not_publish_target_alias():
    registry = {"btn": {"value": "target_1", "type": "target"}}
    fake_executor = types.SimpleNamespace(
        solver=types.SimpleNamespace(variable_registry=registry)
    )
    step = types.SimpleNamespace(output_target_name="btn")

    Executor._drop_failed_target_alias(fake_executor, step)
    assert "btn" not in registry


def test_exact_candidate_is_unique_and_conservative():
    world = ObservedWorld(candidates=[
        ObservedCandidate(key="t1", description="Barre de recherche"),
        ObservedCandidate(key="t2", description="Bouton Rechercher"),
    ])

    match = exact_candidate(world, "barre de recherche")
    assert match is not None and match.key == "t1"

    ambiguous = ObservedWorld(candidates=[
        ObservedCandidate(key="t1", description="Bouton OK"),
        ObservedCandidate(key="t2", description="Bouton OK"),
    ])
    assert exact_candidate(ambiguous, "Bouton OK") is None
    assert exact_candidate(world, "resultats de recherche") is None


def test_widen_crop_analysis_retries_once_and_returns_success(monkeypatch):
    calls = []

    async def fake_analysis(data, query, runtime_state, tag, media_assets=None):
        calls.append((data, media_assets))
        return {"result": True, "data": "vu en entier"}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_analysis)

    out = asyncio.run(IT._widen_crop_analysis(
        query="q",
        runtime_state=types.SimpleNamespace(),
        cropped_out={"result": False, "data": "crop"},
        full_observation={"frame": "full"},
        full_media_assets=None,
    ))

    assert out["result"] is True
    assert out["widened"] is True
    assert len(calls) == 1
    assert calls[0][0] == {"frame": "full"}


def test_widen_crop_analysis_keeps_both_observations_on_failure(monkeypatch):
    async def fake_analysis(data, query, runtime_state, tag, media_assets=None):
        return {"result": False, "data": "full frame illisible", "error_reason": "flou"}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_analysis)

    out = asyncio.run(IT._widen_crop_analysis(
        query="q",
        runtime_state=types.SimpleNamespace(),
        cropped_out={"result": False, "data": "crop"},
        full_observation={"frame": "full"},
        full_media_assets=None,
    ))

    assert out["result"] is False
    assert out["data"] == "crop"
    assert out["widened_attempt"]["data"] == "full frame illisible"


def test_attempt_events_expose_execution_vs_plan(monkeypatch):
    captured = []

    monkeypatch.setattr(
        solver_module.Logger,
        "event",
        staticmethod(lambda name, **fields: captured.append((name, fields))),
    )

    solver = Solver.__new__(Solver)
    solver.id = "solver-1"
    solver.runtime_state = types.SimpleNamespace(
        execution_markers={"execution_attempt": 3}
    )
    rejected = types.SimpleNamespace(
        attempt_number=4,
        plan_id="plan-4",
        outcome="rejected",
        failure_class=solver_module.FailureClass.PLAN_REJECTED_SUPERVISOR,
    )
    executed = types.SimpleNamespace(
        attempt_number=5,
        plan_id="plan-5",
        outcome="failed",
        failure_class=solver_module.FailureClass.EXECUTION_FAILURE,
    )

    solver._emit_attempt_event("attempt_started", rejected)
    solver._emit_attempt_event("attempt_failed", rejected, "refus")
    solver._emit_attempt_event("attempt_failed", executed, "échec")

    started = captured[0][1]
    assert started["execution_attempt"] == 3
    assert started["counts_toward_budget"] is None

    reject_fields = captured[1][1]
    assert reject_fields["counts_toward_budget"] is False
    assert reject_fields["attempt_number"] == 4

    exec_fields = captured[2][1]
    assert exec_fields["counts_toward_budget"] is True


def test_prompts_forbid_semantic_substitution():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "prompts"
    for lang in ("base", "en", "fr"):
        selection = (root / lang / "target_selection.md").read_text(encoding="utf-8")
        assert "no_target=true" in selection
        # P1-5 : la règle de non-substitution doit être explicite.
        assert ("substitution" in selection) or ("substitute" in selection)
        # Aucun nom d'outil concret dans une règle censée être agnostique.
        lowered = selection.lower()
        for tool_name in ("keyboard", "mouse", "click", "clic"):
            assert tool_name not in lowered, (lang, tool_name)

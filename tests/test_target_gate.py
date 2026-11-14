"""Gate de preuve post-producteur (2026-11-14).

Ces bancs figent la correction d'un defaut d'ancrage : un producteur qui
rend une cible `single` ne prouve rien par lui-meme. Le cerveau doit
confronter la nature produite a la question avant tout binding ou action.

Aucun nom d'outil, de champ ou d'application n'est code en dur : tout vient
des contrats declares par l'hote.
"""

import asyncio
import json
import types

from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload
from core.targeting import ObservedWorld, resolve_target
from core.targets import TargetRegistry
from tools import internal_tools as IT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contract(raw, tool_name="scan"):
    parsed = parse_target_contract(raw, tool_name=tool_name)
    assert parsed.contract is not None, parsed.errors
    return parsed.contract


def _candidates_contract(*, disclosure=None, tool_name="scan"):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {
                "ref": "id",
                "description": "name",
                "attributes": {"role": "role"},
            },
        }],
    }
    if disclosure is not None:
        raw["disclosure"] = disclosure
    return _contract(raw, tool_name=tool_name)


def _single_contract(*, tool_name="target"):
    return _contract({
        "version": 1,
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
    }, tool_name=tool_name)


class _Manager:
    def __init__(self, contracts=None, payloads=None, tools=None):
        self.contracts = contracts or {}
        self.payloads = payloads or {}
        self.calls = []
        self._tools = tools or {}

    def get_target_contract(self, name):
        return self.contracts.get(name)

    def get_tool_returns(self, name):
        return []

    async def execute_tool(self, name, args):
        self.calls.append((name, dict(args)))
        result = self.payloads.get(name, {"result": True, "data": {}})
        if callable(result):
            result = result(dict(args))
        return json.dumps(result)


class _ScriptedLlm:
    """Repond au schema recu : selection ou verification, sans ambiguite."""

    def __init__(self, selections, verifications=None):
        self.selections = list(selections)
        self.verifications = list(verifications or [])
        self.prompts = []
        self.tags = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        self.tags.append(tag)
        if schema.__name__ == "TargetVerification":
            answer = self.verifications.pop(0) if self.verifications else {
                "verdict": "uncertain",
                "confidence": "low",
                "reason": "non scripte",
            }
        elif schema.__name__ == "AnalysisResult":
            answer = {"success": True, "data": {"answer": "ok"}}
        else:
            answer = self.selections.pop(0) if self.selections else {
                "no_target": True,
                "reason": "non scripte",
            }
        if callable(answer):
            answer = answer(prompt)
        return schema(**answer)


def _runtime(manager, registry, llm, *, gate=True):
    return types.SimpleNamespace(
        tools_manager=manager,
        target_registry=registry,
        _solver_registry_for_tools={},
        _tools_llm=llm,
        language="fr",
        target_verification_enabled=True,
        target_gate_enabled=gate,
    )


# ---------------------------------------------------------------------------
# 1. resolve_target : strict exige une preuve positive
# ---------------------------------------------------------------------------


def _world_with_one_candidate():
    contract = _candidates_contract()
    registry = TargetRegistry()
    projected = project_target_payload(
        registry,
        contract,
        {"result": True, "data": {"candidates": [
            {"id": "ref-file", "name": "bonjour.txt", "role": "file"},
        ]}},
    )[0]
    world = ObservedWorld.from_payload(registry, contract, projected)
    return contract, registry, world


def test_uncertain_bloque_une_cible_actionnee():
    contract, registry, world = _world_with_one_candidate()
    key = world.candidates[0].key
    llm = _ScriptedLlm(
        selections=[{"target_key": key, "target_kind": "local", "reason": "proche"}],
        verifications=[{
            "verdict": "uncertain",
            "confidence": "low",
            "reason": "preuve insuffisante",
        }],
    )
    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_Manager(contracts={"scan": contract}),
        allow_anchor=False,
        max_rounds=0,
        verify=True,
        strict=True,
    ))
    assert not result.ok
    assert result.error_code == "TARGET_UNPROVEN"
    assert result.gate_status == "unproven"
    assert result.entry is None
    assert result.verification["verdict"] == "uncertain"


def test_mismatch_bloque_une_cible_actionnee():
    contract, registry, world = _world_with_one_candidate()
    key = world.candidates[0].key
    llm = _ScriptedLlm(
        selections=[{"target_key": key, "target_kind": "local", "reason": "proche"}],
        verifications=[{
            "verdict": "mismatch",
            "confidence": "high",
            "reason": "un fichier n'est pas une zone vide",
        }],
    )
    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_Manager(contracts={"scan": contract}),
        allow_anchor=False,
        max_rounds=0,
        verify=True,
        strict=True,
    ))
    assert not result.ok
    assert result.error_code == "TARGET_MISMATCH"
    assert result.gate_status == "contradicted"


def test_match_laisse_passer_la_cible_actionnee():
    contract, registry, world = _world_with_one_candidate()
    key = world.candidates[0].key
    llm = _ScriptedLlm(
        selections=[{"target_key": key, "target_kind": "local", "reason": "exact"}],
        verifications=[{
            "verdict": "match",
            "confidence": "high",
            "reason": "fichier demande",
        }],
    )
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le fichier bonjour.txt",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_Manager(contracts={"scan": contract}),
        allow_anchor=False,
        max_rounds=0,
        verify=True,
        strict=True,
    ))
    assert result.ok, result.error_message
    assert result.entry is not None
    assert result.gate_status == "proven"


# ---------------------------------------------------------------------------
# 2. Une ancre consommee par un producteur est inspectee avant usage
# ---------------------------------------------------------------------------


def _locator_disclosure():
    return {
        "version": 1,
        "reliability": "low",
        "evidence": ["metadata"],
        "anchors": [
            {"kind": "cell", "param": "cell", "description": "Cellule brute."},
        ],
        "probes": [{
            "id": "inspect_cell",
            "kind": "inspect",
            "description": "Inspecte une cellule.",
            "tool": "zoom",
            "subject": {"kind": "cell", "param": "cell"},
            "produces": ["candidates"],
        }],
    }


def test_ancre_localisatrice_est_inspectee_avant_consommation():
    contract = _candidates_contract(disclosure=_locator_disclosure())
    registry = TargetRegistry()
    projected = project_target_payload(
        registry,
        contract,
        {"result": True, "data": {"candidates": [
            {"id": "ref-old", "name": "vieux", "role": "file"},
        ]}},
    )[0]
    world = ObservedWorld.from_payload(registry, contract, projected)
    manager = _Manager(
        contracts={"scan": contract},
        payloads={
            "zoom": {"result": True, "data": {"candidates": [
                {"id": "ref-ok", "name": "bouton ok", "role": "button"},
            ]}},
        },
    )
    llm = _ScriptedLlm(
        selections=[
            {
                "target_key": "B3",
                "target_kind": "anchor",
                "anchor_kind": "cell",
                "reason": "cellule supposee",
            },
            lambda prompt: {
                "target_key": next(
                    line[2:].split(":", 1)[0].strip()
                    for line in prompt.splitlines()
                    if line.strip().startswith("- ") and "bouton ok" in line
                ),
                "target_kind": "local",
                "reason": "cible interne prouvee",
            },
        ],
        verifications=[{
            "verdict": "match",
            "confidence": "high",
            "reason": "bouton ok",
        }],
    )
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le bouton ok",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=True,
        anchor_kinds=["cell"],
        max_rounds=3,
        verify=True,
        strict=True,
        anchor_is_locator=True,
    ))
    assert result.ok, result.error_message
    # L'ancre a ete inspectee AVANT d'etre consommee.
    assert ("zoom", {"cell": "B3"}) in manager.calls
    # Le selecteur a re-decide avec l'inspection visible.
    assert any("inspect_cell" in prompt for prompt in llm.prompts[1:])
    assert result.entry is not None
    assert result.entry.description == "bouton ok"
    assert result.gate_status == "proven"


# ---------------------------------------------------------------------------
# 3. Gate post-producteur sur une cible `single`
# ---------------------------------------------------------------------------


def _single_runtime(verdict):
    contract = _single_contract()
    manager = _Manager(
        contracts={"target": contract},
        payloads={
            "target": {"result": True, "data": {"target": {
                "ref": {"kind": "ui", "id": "9"},
                "label": "chats.json - Raccourci",
                "corr": "c1",
                "gen": 1,
            }}},
        },
        tools={"target": {"kind": "utility"}},
    )
    registry = TargetRegistry()
    llm = _ScriptedLlm(
        selections=[],
        verifications=[verdict],
    )
    return _runtime(manager, registry, llm), manager, registry


def test_single_mismatch_refuse_le_binding(monkeypatch):
    events = []
    monkeypatch.setattr(
        IT.Logger, "event", lambda name, **fields: events.append((name, fields))
    )
    verdict = {
        "verdict": "mismatch",
        "confidence": "high",
        "reason": "un raccourci de fichier n'est pas une option de menu",
    }
    runtime, manager, registry = _single_runtime(verdict)
    out = asyncio.run(IT.perceive_understand({
        "question": "trouve l'option Trier par",
        "target_producer": "target",
        "target_args": {"cell": "F1"},
        "_output_target_name": "option_tri",
    }, runtime))
    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_UNPROVEN"
    assert "option_tri" not in runtime._solver_registry_for_tools
    assert out.get("target") is None
    # Le gate est visible dans l'observabilite, meme en refus.
    gates = [fields for name, fields in events if name == "target.gate"]
    assert gates and gates[-1]["status"] == "contradicted"


def test_single_uncertain_refuse_le_binding():
    verdict = {
        "verdict": "uncertain",
        "confidence": "low",
        "reason": "preuves insuffisantes",
    }
    runtime, manager, registry = _single_runtime(verdict)
    out = asyncio.run(IT.perceive_understand({
        "question": "trouve l'option Trier par",
        "target_producer": "target",
        "target_args": {"cell": "F1"},
        "_output_target_name": "option_tri",
    }, runtime))
    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_UNPROVEN"
    assert "option_tri" not in runtime._solver_registry_for_tools


def test_single_match_autorise_le_binding():
    verdict = {
        "verdict": "match",
        "confidence": "high",
        "reason": "le raccourci correspond",
    }
    runtime, manager, registry = _single_runtime(verdict)
    out = asyncio.run(IT.perceive_understand({
        "question": "ouvre chats.json",
        "target_producer": "target",
        "target_args": {"preset": "t1"},
        "_output_target_name": "raccourci",
    }, runtime))
    assert out["result"] is True, out
    assert out["target"]["key"]
    assert "raccourci" in runtime._solver_registry_for_tools


def test_single_sans_gate_garde_le_comportement_historique():
    verdict = {
        "verdict": "mismatch",
        "confidence": "high",
        "reason": "peu importe",
    }
    runtime, manager, registry = _single_runtime(verdict)
    runtime.target_gate_enabled = False
    out = asyncio.run(IT.perceive_understand({
        "question": "trouve l'option Trier par",
        "target_producer": "target",
        "target_args": {"cell": "F1"},
        "_output_target_name": "option_tri",
    }, runtime))
    assert out["result"] is True
    assert "option_tri" in runtime._solver_registry_for_tools


# ---------------------------------------------------------------------------
# 4. Gate sur le chemin producteur DIRECT du plan (core.executor)
# ---------------------------------------------------------------------------


def _executor_runtime(tmp_path, verdict):
    """Runtime minimal pour `Executor._handle_tool_call` (producteur direct)."""
    from tools.tools_manager import ToolsManager

    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="target",
        role="action",
        description="target",
        parameters_schema={
            "type": "object",
            "properties": {"cell": {"type": "string"}},
            "required": ["cell"],
        },
        target_contract={
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
        },
    )
    llm = _ScriptedLlm(selections=[], verifications=[verdict])
    runtime = types.SimpleNamespace(
        session_id="sess-executor",
        discovery_engine=None,
        target_registry=TargetRegistry(),
        tools_manager=tm,
        _tools_llm=llm,
        language="fr",
        target_verification_enabled=True,
        target_gate_enabled=True,
    )

    async def _execute_tool(name, args):
        return json.dumps({
            "result": True,
            "data": {"target": {
                "ref": "raw-ref-f1",
                "label": "chats.json - Raccourci",
                "corr": "corr-f1",
                "gen": 1,
            }},
        })

    solver = types.SimpleNamespace(
        id="s-executor",
        variable_registry={},
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=runtime,
        execute_tool=_execute_tool,
    )
    return runtime, solver


def _run_direct_producer(solver, description):
    from core.executor import Executor
    from core.plan_models import PlanStep, StepType

    step = PlanStep(
        id="step_4",
        description=description,
        type=StepType.TOOL_CALL,
        tool_name="target",
        tool_args_json=json.dumps({"cell": "F1"}),
        expected_result="true",
        output_target_name="option_tri",
    )
    executor = Executor.__new__(Executor)
    executor.solver = solver
    node = types.SimpleNamespace(metadata={})
    return asyncio.run(executor._handle_tool_call(step, node=node))


def test_producteur_direct_mismatch_refuse_le_binding(tmp_path):
    runtime, solver = _executor_runtime(
        tmp_path,
        {
            "verdict": "mismatch",
            "confidence": "high",
            "reason": "un raccourci de fichier n'est pas une option de menu",
        },
    )
    ok, _output, error = _run_direct_producer(
        solver, "trouve l'option qui trie la liste"
    )
    assert ok is False
    assert "non prouvee" in (error or "").lower() or "preuve positive" in (error or "").lower()
    assert "option_tri" not in solver.variable_registry


def test_producteur_direct_uncertain_refuse_le_binding(tmp_path):
    runtime, solver = _executor_runtime(
        tmp_path,
        {
            "verdict": "uncertain",
            "confidence": "low",
            "reason": "preuves insuffisantes",
        },
    )
    ok, _output, error = _run_direct_producer(
        solver, "trouve l'option qui trie la liste"
    )
    assert ok is False
    assert "option_tri" not in solver.variable_registry


def test_producteur_direct_match_autorise_le_binding(tmp_path):
    runtime, solver = _executor_runtime(
        tmp_path,
        {
            "verdict": "match",
            "confidence": "high",
            "reason": "la cible est bien l'option demandee",
        },
    )
    ok, _output, error = _run_direct_producer(
        solver, "trouve l'option qui trie la liste"
    )
    assert ok is True, error
    binding = solver.variable_registry["option_tri"]
    assert binding["type"] == "target"
    assert "raw-ref-f1" not in json.dumps(binding)


# ---------------------------------------------------------------------------
# 5. La cible PRODUITE expose ses faits declares au verificateur
# ---------------------------------------------------------------------------


def _enriched_single_contract():
    """Producteur dont `data.target` porte des faits declares (invariants)."""
    return _contract({
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
                "attributes": {
                    "kind": "kind",
                    "rect": "rect",
                    "point": "point",
                    "kind_reason": "kind_reason",
                    "invariants": "invariants",
                    "selection": "selection",
                },
            },
        }],
        "disclosure": {
            "version": 1,
            "reliability": "high",
            "evidence": ["metadata", "rect", "nested_targets", "invariants"],
        },
    }, tool_name="target")


def _enriched_payload():
    return {"result": True, "data": {"target": {
        "ref": {"kind": "ui", "id": "9"},
        "label": "chats.json - Raccourci",
        "corr": "c1",
        "gen": 1,
        "kind": "shortcut",
        "rect": [500, 30, 64, 20],
        "point": [532, 39],
        "kind_reason": "uia_control_type",
        "invariants": {
            "provenance": "foreground_fallback",
            "window": "desktop",
            "rect_semantics": "uia_bounds",
            "action_point": "center",
            "enumerable": True,
        },
        "selection": {
            "chosen": "chats.json (ListItem)",
            "reason": "contains_center_deepest",
            "role_changed": True,
            "candidates": [{"name": "chats.json", "role": "ListItem", "score": 4, "accepted": True}],
        },
    }}}


def test_cible_produite_expose_ses_faits_declares_au_verificateur():
    from core.target_ingress import collect_projected_targets

    contract = _enriched_single_contract()
    registry = TargetRegistry()
    payload = _enriched_payload()
    projected, _count = project_target_payload(registry, contract, payload)
    entries = collect_projected_targets(registry, contract, projected)
    assert len(entries) == 1
    entry = entries[0]
    # La projection lit bien `fields.attributes` sur la cible produite.
    assert entry.attributes.get("invariants", {}).get("provenance") == "foreground_fallback"
    assert entry.attributes.get("selection", {}).get("role_changed") is True

    llm = _ScriptedLlm(
        selections=[],
        verifications=[{
            "verdict": "match",
            "confidence": "high",
            "reason": "cible demandee",
        }],
    )
    runtime = types.SimpleNamespace(
        tools_manager=_Manager(contracts={"target": contract}),
        target_registry=registry,
        _solver_registry_for_tools={},
        _tools_llm=llm,
        language="fr",
        target_verification_enabled=True,
        target_gate_enabled=True,
    )
    outcome, error = asyncio.run(IT._gate_produced_target(
        runtime,
        entry=entry,
        question="ouvre le fichier chats.json",
        contract=contract,
        payload=projected,
        media_assets=None,
        disclosure=contract.disclosure,
        source="target",
    ))
    assert error is None
    assert outcome.verdict == "match"
    # Les faits declares atteignent reellement le verificateur, sans qu'aucune
    # cle d'hote ne soit codee en dur cote cerveau.
    prompt = llm.prompts[-1]
    assert "foreground_fallback" in prompt
    assert "uia_bounds" in prompt
    assert "role_changed" in prompt
    assert "shortcut" in prompt
    assert "532" in prompt

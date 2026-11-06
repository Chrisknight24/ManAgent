"""Phases 2→5 : ancrage par producteur, composite, warnings et alignement.

Ces tests verrouillent les décisions du chantier du 04/11 :

- le planner ne devine plus de référence : il désigne un producteur déclaré ;
- `perceive_action` est un composite `perceive_understand` + action ;
- un `false` de perception est un échec d'ALIGNEMENT, jamais une panne ;
- les warnings déterministes ne bloquent rien.
"""

import asyncio
import json
import types

from core.execution_models import FailureClass
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import (
    PlanValidationOutcome,
    find_invalid_perceive_sources,
    find_plan_without_action,
    find_target_plan_problems,
)
from core.solver import _perception_stall, _update_tool_health
from core.target_contract import parse_target_contract
from core.targets import TargetRegistry
from tools import internal_tools as IT


def _tool_step(sid, tool, args):
    return PlanStep(
        id=sid,
        description="d",
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json=args,
        expected_result="true",
    )


def _producer_contract(tool_name="find", mode="single"):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.targets",
            "mode": mode,
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }],
    }
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


def _consumer_contract(param="target", tool_name="click"):
    raw = {
        "version": 1,
        "consumes": [{"param": param, "scope": "ui_element"}],
    }
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


class _ProducerManager:
    def __init__(self, producer, payload, kind="utility"):
        self.calls = []
        self._tools = {
            producer: {
                "name": producer,
                "kind": kind,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        }
        self._contracts = {producer: _producer_contract(producer)}
        self._payload = payload

    def get_target_contract(self, name):
        return self._contracts.get(name)

    def get_tool_returns(self, name):
        return []

    def validate_tool_call(self, name, args):
        return True

    async def execute_tool(self, name, args):
        self.calls.append((name, dict(args)))
        return json.dumps(self._payload)


def _runtime(manager, registry, variables=None):
    return types.SimpleNamespace(
        tools_manager=manager,
        target_registry=registry,
        _solver_registry_for_tools=variables if variables is not None else {},
        language="en",
    )


def test_find_plan_without_action_ignore_les_lectures():
    lecture = Plan(goal="g", steps=[
        _tool_step("s1", "perceive_understand", '{"question": "q", "source_tool": "vision"}'),
    ])
    action = Plan(goal="g", steps=[
        _tool_step("s1", "mouse", '{"action": "click", "target": "$@_x"}'),
    ])
    vide = Plan(goal="g", steps=[])
    assert find_plan_without_action(lecture) is True
    assert find_plan_without_action(action) is False
    assert find_plan_without_action(vide) is False


def test_perception_stall_apres_trois_desalignements():
    align = [("alignment_failure :: cible absente", 0)] * 3
    mixte = [("alignment_failure :: a", 0), ("execution_failure :: b", 0)] * 2
    assert _perception_stall(align) is True
    assert _perception_stall(mixte) is False
    assert _perception_stall(align[:2]) is False


def test_update_tool_health_ignore_les_outils_d_alignement():
    def attempt(tool):
        node = types.SimpleNamespace(status="failed", tool_name=tool)
        return types.SimpleNamespace(outcome="failed", nodes=[node])

    perception = {"perceive_understand": 2}
    assert _update_tool_health(perception, [attempt("perceive_understand")]) == []
    assert perception["perceive_understand"] == 0

    action = {"mouse": 2}
    assert _update_tool_health(action, [attempt("mouse")]) == ["mouse"]


def test_outcome_conserve_les_warnings_non_bloquants():
    outcome = PlanValidationOutcome(
        is_valid=True,
        reason="ok",
        warnings=["perception non consommée"],
    )
    assert outcome.is_valid is True
    assert bool(outcome) is True
    assert outcome.warnings == ["perception non consommée"]


def test_perceive_understand_ancre_via_producteur_declare():
    producer = "find_control"
    payload = {
        "result": True,
        "data": {"targets": {"id": "ref-prod", "label": "Bouton OK"}},
    }
    manager = _ProducerManager(producer, payload)
    registry = TargetRegistry()
    variables = {}
    runtime = _runtime(manager, registry, variables)

    out = asyncio.run(IT.perceive_understand({
        "question": "trouve le bouton OK",
        "target_producer": producer,
        "target_args": {"query": "Bouton OK"},
        "target_description": "le bouton OK",
        "_output_target_name": "bouton_ok",
        "_anchor_only": True,
    }, runtime))

    entry = registry.find_by_ref("ref-prod")
    assert out["result"] is True
    assert manager.calls == [(producer, {"query": "Bouton OK"})]
    assert out["target"]["key"] == entry.local_name
    assert out["target_name"] == "bouton_ok"
    assert variables["bouton_ok"]["value"] == entry.local_name
    assert "ref-prod" not in json.dumps(variables)


def test_perceive_understand_refuse_un_producteur_non_utility():
    manager = _ProducerManager(
        "find_control",
        {"result": True, "data": {"targets": {"id": "r", "label": "x"}}},
        kind="action",
    )
    out = asyncio.run(IT.perceive_understand({
        "question": "trouve",
        "target_producer": "find_control",
        "target_args": {"query": "x"},
        "_anchor_only": True,
    }, _runtime(manager, TargetRegistry())))
    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_PRODUCER_NOT_UTILITY"
    assert manager.calls == []


def test_perceive_action_refuse_target_dans_un_parametre_non_declare():
    producer = "find_control"
    payload = {
        "result": True,
        "data": {"targets": {"id": "ref-prod", "label": "Bouton"}},
    }

    class Manager(_ProducerManager):
        def __init__(self):
            super().__init__(producer, payload)
            self._contracts["click"] = _consumer_contract(param="target")

    manager = Manager()
    runtime = _runtime(manager, TargetRegistry())
    out = asyncio.run(IT.perceive_action({
        "question": "clique",
        "target_producer": producer,
        "target_args": {"query": "Bouton"},
        "action_tool": "click",
        "action_args": {"cell": "$TARGET"},
    }, runtime))
    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_SCHEMA_INVALID"
    assert [name for name, _ in manager.calls] == [producer]


def test_plan_valide_ancre_via_target_producer():
    from tools.tools_manager import ToolsManager

    def schema(*params):
        return {
            "type": "object",
            "properties": {name: {"type": "string"} for name in params},
            "required": list(params),
        }

    raw_producer = {
        "version": 1,
        "produces": [{
            "path": "data.targets",
            "mode": "single",
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }],
    }
    raw_consumer = {
        "version": 1,
        "consumes": [{"param": "target", "scope": "ui_element"}],
    }
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        "find_control", "r", "find", schema("query"), kind="utility",
        target_contract=raw_producer,
    )
    tm.register_tool(
        "click", "r", "click", schema("target"),
        target_contract=raw_consumer,
    )
    plan = Plan(goal="g", steps=[
        PlanStep(
            id="s1",
            description="ancre",
            type=StepType.TOOL_CALL,
            tool_name="perceive_understand",
            tool_args_json=json.dumps({
                "question": "trouve le bouton",
                "target_producer": "find_control",
                "target_args": {"query": "Bouton"},
                "target_description": "le bouton",
            }),
            output_target_name="btn",
            expected_result="true",
        ),
        _tool_step("s2", "click", '{"target": "$@_btn"}'),
    ])
    assert find_target_plan_problems(plan, tm) == []


def test_find_invalid_perceive_sources_accepte_un_producteur_connu():
    plan = Plan(goal="g", steps=[
        _tool_step(
            "s1",
            "perceive_understand",
            '{"question": "q", "target_producer": "find_control", "target_args": {}}',
        ),
    ])
    assert find_invalid_perceive_sources(plan, {"find_control"}) == []
    bad = find_invalid_perceive_sources(plan, {"autre"})
    assert bad and "find_control" in bad[0]


def test_alignment_failure_est_une_classe_distincte():
    assert FailureClass.ALIGNMENT_FAILURE.value == "alignment_failure"
    assert FailureClass.ALIGNMENT_FAILURE != FailureClass.EXECUTION_FAILURE


def test_deux_canaux_de_warnings_ne_se_melangent_jamais():
    """Lacunes partielles = porte déterministe ; warnings = juge seulement."""
    from core.orchestrator import store_plan_warnings
    from core.solver import gap_convergence_refusal, plan_convergence_warnings

    runtime = types.SimpleNamespace()
    stored = store_plan_warnings(
        runtime,
        "solver-1",
        partial_gaps=["le fichier B n'est pas vérifié"],
        warnings=["ancrage faible sur la cible", "perception non consommée"],
    )

    assert stored["gaps"] == ["le fichier B n'est pas vérifié"]
    assert runtime.plan_warnings_by_solver["solver-1"] == [
        "le fichier B n'est pas vérifié"
    ]
    assert runtime.plan_soft_warnings_by_solver["solver-1"] == [
        "ancrage faible sur la cible",
        "perception non consommée",
    ]

    gaps, text = plan_convergence_warnings(runtime, "solver-1")
    # La porte déterministe ne voit QUE les lacunes.
    assert gaps == ["le fichier B n'est pas vérifié"]
    # Le juge final, lui, voit les avertissements.
    assert "ancrage faible sur la cible" in text
    assert "(avertissement)" in text
    assert gap_convergence_refusal(
        gaps, perception_proofs=[], perception_tools_available=True
    )

    # Aucune lacune : aucun avertissement ne peut déclencher la porte.
    runtime.plan_warnings_by_solver = {}
    gaps, text = plan_convergence_warnings(runtime, "solver-1")
    assert gaps == []
    assert "ancrage faible sur la cible" in text
    assert gap_convergence_refusal(
        gaps, perception_proofs=[], perception_tools_available=True
    ) is None


def test_store_plan_warnings_efface_les_canaux_quand_tout_est_vide():
    from core.orchestrator import store_plan_warnings

    runtime = types.SimpleNamespace(
        plan_warnings_by_solver={"solver-1": ["ancienne lacune"]},
        plan_soft_warnings_by_solver={"solver-1": ["ancien warning"]},
    )
    store_plan_warnings(runtime, "solver-1", partial_gaps=[], warnings=[])
    assert "solver-1" not in runtime.plan_warnings_by_solver
    assert "solver-1" not in runtime.plan_soft_warnings_by_solver

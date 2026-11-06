"""Phase 2b : flux composite source -> candidate -> producteur -> action.

L'hote du 05/11 ne cherche plus une cible par son nom : il expose des
candidates numerotees (`t1`, `t2`, ...), reinitialisees a chaque capture, et
accepte `target preset=tN`. Le cerveau ne doit donc JAMAIS ecrire `tN` dans
un plan : il lit les candidates et les consomme dans la MEME etape.

Ce banc rejoue la chaine complete sur `HostSim` (vrais ToolsManager,
TargetRegistry, Orchestrator) :

    vision (candidates) -> choix par description -> target preset=<local>
    -> cible fraiche -> mouse target=<local> -> reference hote brute

Aucune reference hote ne sort vers le plan ni vers l'action.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import (
    find_invalid_perceive_sources,
    find_target_plan_problems,
)
from host_sim import HostSim
from tools import internal_tools as IT
from tools.tools_manager import ToolsManager


class _PickByDescriptionLLM:
    """Choisit la candidate dont la description contient le mot demande.

    Le prompt de selection liste `cle_locale: description (scope=...)` ;
    le faux LLM lit cette liste, comme le ferait un vrai modele.
    """

    def __init__(self, needle):
        self.needle = needle.lower()
        self.prompts = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        for line in prompt.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            key, _, rest = line[2:].partition(":")
            if self.needle in rest.lower():
                return schema(target_key=key.strip(), reason="test")
        return schema(target_key="", reason="aucune candidate")


def _manifest(*, target_consumes=True, consumer_scope="ui_preset"):
    target_contract = {
        "version": 1,
        "produces": [{
            "path": "data.target",
            "mode": "single",
            "scope": "ui_target",
            "fields": {"ref": "id", "description": "name"},
        }],
    }
    if target_consumes:
        target_contract["consumes"] = [{
            "param": "preset",
            "scope": consumer_scope,
            "required": False,
        }]
    return {
        "host_name": "hostsim-presets",
        "host_version": "1.0.0",
        "tools": [
            {
                "name": "vision",
                "kind": "utility",
                "description": "Capture l'ecran et renvoie des presets numerotes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "max_presets": {"type": "integer"},
                    },
                    "required": [],
                },
                "target_contract": {
                    "version": 1,
                    "produces": [{
                        "path": "data.presets",
                        "mode": "candidates",
                        "scope": "ui_preset",
                        "fields": {"ref": "id", "description": "name"},
                    }],
                },
            },
            {
                "name": "target",
                "kind": "utility",
                "description": "Cree une cible fraiche depuis un preset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "preset": {"type": "string"},
                        "resolution": {"type": "string"},
                    },
                    "required": [],
                },
                "target_contract": target_contract,
            },
            {
                "name": "mouse",
                "kind": "action",
                "description": "Action souris sur une cible nommee.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["action", "target"],
                },
                "target_contract": {
                    "version": 1,
                    "consumes": [{"param": "target", "scope": "ui_target"}],
                },
            },
        ],
    }


def _vision_result(*names):
    return {
        "result": True,
        "data": {
            "presets": [
                {"id": f"t{index}", "name": name}
                for index, name in enumerate(names, start=1)
            ],
            "presets_total": len(names),
            "presets_truncated": False,
        },
    }


def _host_results(*, names=("OK", "Annuler"), target_result=None):
    return {
        "vision": _vision_result(*names),
        "target": target_result or {
            "result": True,
            "data": {"target": {"id": "ref-ok", "name": "Bouton OK"}},
        },
        "mouse": lambda args: {
            "result": True,
            "data": {"action": args.get("action")},
        },
    }


def _sim(results, manifest=None):
    sim = HostSim(results=results)
    sim.runtime_state._solver_registry_for_tools = {}
    asyncio.run(sim.register_manifest(manifest or _manifest()))
    return sim


def _composite_call(**overrides):
    call = {
        "question": "le bouton OK de la fenetre Exemple",
        "source_tool": "vision",
        "source_args": {"mode": "image"},
        "target_producer": "target",
        "target_args": {},
        "target_description": "le bouton OK",
        "action_tool": "mouse",
        "action_args": {"action": "click", "target": "$TARGET"},
    }
    call.update(overrides)
    return call


def _calls(sim, tool_name):
    return [call for call in sim.host_calls if call["tool_name"] == tool_name]


def test_composite_lit_les_candidates_et_consomme_la_bonne_dans_la_meme_etape():
    sim = _sim(_host_results())
    sim.runtime_state._tools_llm = _PickByDescriptionLLM("OK")

    out = asyncio.run(IT.perceive_action(_composite_call(), sim.runtime_state))

    assert out["result"] is True, out
    assert len(_calls(sim, "vision")) == 1
    assert _calls(sim, "vision")[0]["arguments"] == {"mode": "image"}

    # Le producteur recoit la REFERENCE HOTE de la candidate choisie (`t1`),
    # jamais un numero ecrit dans le plan ; l'affichage garde le nom local.
    target_call = _calls(sim, "target")[0]
    assert target_call["arguments"]["preset"] == "t1"
    assert target_call["arguments_display"]["preset"] != "t1"
    assert target_call["arguments_display"]["preset"]

    # L'action recoit la reference de la cible FRAICHE produite.
    mouse_call = _calls(sim, "mouse")[0]
    assert mouse_call["arguments"]["target"] == "ref-ok"
    assert mouse_call["arguments_display"]["target"] != "ref-ok"

    for call in sim.host_calls:
        assert "$@_" not in json.dumps(call["arguments"])
        assert "$TARGET" not in json.dumps(call["arguments"])


def test_composite_ne_devine_rien_si_la_source_n_a_aucune_candidate():
    sim = _sim(_host_results(names=()))
    sim.runtime_state._tools_llm = _PickByDescriptionLLM("OK")

    out = asyncio.run(IT.perceive_action(_composite_call(), sim.runtime_state))

    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_ANCHOR_UNSUPPORTED"
    assert _calls(sim, "target") == []
    assert _calls(sim, "mouse") == []


def test_composite_refuse_un_producteur_sans_consommateur_declare():
    sim = _sim(_host_results(), manifest=_manifest(target_consumes=False))
    sim.runtime_state._tools_llm = _PickByDescriptionLLM("OK")

    out = asyncio.run(IT.perceive_action(_composite_call(), sim.runtime_state))

    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_ANCHOR_UNSUPPORTED"
    assert _calls(sim, "target") == []
    assert _calls(sim, "mouse") == []


def test_composite_refuse_une_candidate_hors_du_scope_du_consommateur():
    sim = _sim(
        _host_results(),
        manifest=_manifest(consumer_scope="ui_autre_scope"),
    )
    sim.runtime_state._tools_llm = _PickByDescriptionLLM("OK")

    out = asyncio.run(IT.perceive_action(_composite_call(), sim.runtime_state))

    assert out["result"] is False
    assert out["error"]["code"] == "TARGET_ANCHOR_UNSUPPORTED"
    assert _calls(sim, "target") == []


def test_target_args_explicite_est_preserve_et_seul_le_consommateur_est_injecte():
    sim = _sim(_host_results())
    sim.runtime_state._tools_llm = _PickByDescriptionLLM("OK")

    out = asyncio.run(IT.perceive_action(
        _composite_call(target_args={"resolution": "preset"}),
        sim.runtime_state,
    ))

    assert out["result"] is True, out
    target_call = _calls(sim, "target")[0]
    assert target_call["arguments"]["resolution"] == "preset"
    assert target_call["arguments"]["preset"] == "t1"


def test_le_prompt_de_selection_recoit_les_descriptions_des_candidates():
    sim = _sim(_host_results(names=("OK", "Annuler")))
    llm = _PickByDescriptionLLM("OK")
    sim.runtime_state._tools_llm = llm

    asyncio.run(IT.perceive_action(_composite_call(), sim.runtime_state))

    assert llm.prompts, "la selection doit interroger le LLM"
    assert "OK" in llm.prompts[0]
    assert "Annuler" in llm.prompts[0]


def _schema(*params, required=None):
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in params},
        "required": list(params if required is None else required),
    }


def _tools_manager(*, target_consumes=True):
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        "vision", "r", "capture", _schema("mode", required=[]),
        kind="utility",
        target_contract={
            "version": 1,
            "produces": [{
                "path": "data.presets",
                "mode": "candidates",
                "scope": "ui_preset",
                "fields": {"ref": "id", "description": "name"},
            }],
        },
    )
    target_contract = {
        "version": 1,
        "produces": [{
            "path": "data.target",
            "mode": "single",
            "scope": "ui_target",
            "fields": {"ref": "id", "description": "name"},
        }],
    }
    if target_consumes:
        target_contract["consumes"] = [{
            "param": "preset", "scope": "ui_preset", "required": False,
        }]
    tm.register_tool(
        "target", "r", "anchor", _schema("preset", "resolution", required=[]),
        kind="utility", target_contract=target_contract,
    )
    tm.register_tool(
        "mouse", "r", "click", _schema("action", "target"),
        target_contract={
            "version": 1,
            "consumes": [{"param": "target", "scope": "ui_target"}],
        },
    )
    return tm


def _composite_plan():
    return Plan(goal="g", steps=[
        PlanStep(
            id="s1",
            description="ancre et clique",
            type=StepType.TOOL_CALL,
            tool_name="perceive_action",
            tool_args_json=json.dumps({
                "question": "le bouton OK",
                "source_tool": "vision",
                "source_args": {"mode": "image"},
                "target_producer": "target",
                "target_args": {},
                "target_description": "le bouton OK",
                "action_tool": "mouse",
                "action_args": {"action": "click", "target": "$TARGET"},
            }),
            expected_result="any",
        ),
        PlanStep(
            id="s2",
            description="verifie",
            type=StepType.TOOL_CALL,
            tool_name="perceive_understand",
            tool_args_json=json.dumps({
                "question": "le bouton OK a-t-il ete active ?",
                "source_tool": "vision",
                "source_args": {"mode": "image"},
            }),
            expected_result="true",
        ),
    ])


def test_le_plan_composite_est_valide_avec_les_declarations_hote():
    tm = _tools_manager()
    plan = _composite_plan()
    assert find_target_plan_problems(plan, tm) == []
    assert find_invalid_perceive_sources(plan, {"vision", "target", "mouse"}) == []


def test_le_plan_composite_est_refuse_si_le_producteur_ne_consomme_rien():
    tm = _tools_manager(target_consumes=False)
    problems = find_target_plan_problems(_composite_plan(), tm)
    assert problems
    assert any("consommateur" in problem for problem in problems)

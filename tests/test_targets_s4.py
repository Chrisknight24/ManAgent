"""S4 : validation de plan cible, def-use parametre-aware et surface prompts."""

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_target_plan_problems
from core.prompt_capabilities import target_prompt_flags
from core.prompt_loader import get_prompt_loader
from core.targets import TargetRegistry
from tools.tools_manager import ToolsManager


CONSUMER = {
    "version": 1,
    "consumes": [{"param": "target", "scope": "ui_element"}],
}
CONSUMER_RAW = {
    "version": 1,
    "consumes": [{"param": "target", "scope": "ui_element", "raw": "allow"}],
}
CANDIDATES = {
    "version": 1,
    "produces": [
        {
            "path": "data.targets",
            "mode": "candidates",
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }
    ],
}
SINGLE = {
    "version": 1,
    "produces": [
        {
            "path": "data.target",
            "mode": "single",
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }
    ],
}


def _schema(*params):
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in params},
        "required": list(params),
    }


def _manager():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        "scan", "r", "scan", _schema("query"), kind="perception",
        target_contract=CANDIDATES,
    )
    tm.register_tool(
        "focus", "r", "focus", _schema("query"), kind="perception",
        target_contract=SINGLE,
    )
    tm.register_tool(
        "click", "r", "click", _schema("target", "note"),
        target_contract=CONSUMER,
    )
    tm.register_tool(
        "click_raw", "r", "click raw", _schema("target"),
        target_contract=CONSUMER_RAW,
    )
    return tm


def _step(step_id, tool_name, args, **fields):
    return PlanStep(
        id=step_id,
        description="d",
        type=StepType.TOOL_CALL,
        expected_result="true",
        tool_name=tool_name,
        tool_args_json=json.dumps(args),
        **fields,
    )


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def _prompt_context(**overrides):
    context = {
        "goal": "g",
        "strategy": "s",
        "context": "c",
        "advice": "",
        "variable_registry": {},
        "tools": [],
        "skills": "",
        "model_id": "m",
        "supported_modalities": [],
        "unsupported_modalities": [],
        "similar_missions": "",
        "tools_guidance": "",
        "registry": "",
        "plan_summary": "s",
        "rules": "r",
        "pattern_warning": None,
        "novelty_assessment": "",
        "repetition_fact": "",
        "direct_perception_note": "",
        "mission_history_summary": "",
        "declared_irreversible_steps": [],
        "hitl_policy": "balanced",
        "hitl_policy_text": "",
        "human_validation_history": "",
        "availability_summary": "",
        "user_message": "u",
        "history": "",
        "world_snapshot": "",
        "world_warnings": [],
        "known_facts": "",
        "session_mission_list": [],
        "known_signatures": [],
        "technical_goals": [],
        "data_types_info": {},
        "old_meta_plan": {},
        "trees": [],
        "targets": [],
        "world_guidance": True,
        "targets_enabled": False,
        "target_selection_enabled": False,
    }
    context.update(overrides)
    return context


def test_s4_alias_accepte_uniquement_dans_un_consommateur():
    tm = _manager()
    ok = _plan(
        _step("s1", "focus", {"query": "bouton"}, output_target_name="btn"),
        _step("s2", "click", {"target": "$@_btn"}),
    )
    assert find_target_plan_problems(ok, tm) == []

    bad = _plan(
        _step("s1", "focus", {"query": "bouton"}, output_target_name="btn"),
        _step("s2", "click", {"target": "$@_btn", "note": "$@_btn"}),
    )
    assert any("hors argument consommateur" in hit for hit in find_target_plan_problems(bad, tm))


def test_s4_alias_futur_ou_non_consommateur_refuse_avant_execution():
    tm = _manager()
    future = _plan(
        _step("s1", "click", {"target": "$@_btn"}),
        _step("s2", "focus", {"query": "bouton"}, output_target_name="btn"),
    )
    assert any("avant sa production" in hit for hit in find_target_plan_problems(future, tm))

    unknown = _plan(_step("s1", "click", {"target": "$@_absent"}))
    assert find_target_plan_problems(unknown, tm) == []


def test_s4_output_target_name_exige_contrat_et_producteur():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool("click", "r", "click", _schema("target"))
    plan = _plan(
        _step("s1", "click", {"target": ""}, output_target_name="btn"),
    )
    hits = find_target_plan_problems(plan, tm)
    assert any("target_contract actif" in hit for hit in hits)
    assert any("interdit" in hit for hit in hits)


def test_s4_charset_prefixe_collision_et_nom_variable():
    tm = _manager()
    bad_charset = _plan(_step("s1", "focus", {}, output_target_name="bad-name"))
    assert any("invalide" in hit for hit in find_target_plan_problems(bad_charset, tm))

    prefixed = _plan(_step("s1", "focus", {}, output_target_name="data_btn"))
    assert any("prefixe" in hit for hit in find_target_plan_problems(prefixed, tm))

    spaced = _plan(_step("s1", "focus", {}, output_target_name=" btn "))
    assert any("espaces" in hit for hit in find_target_plan_problems(spaced, tm))

    duplicate = _plan(
        _step("s1", "focus", {}, output_target_name="btn"),
        _step("s2", "focus", {}, output_target_name="btn"),
    )
    assert any("collision de cible" in hit for hit in find_target_plan_problems(duplicate, tm))

    collision_var = _plan(
        _step("s1", "focus", {}, output_target_name="btn", output_variable_name="data_btn"),
    )
    assert any("cible et variable" in hit for hit in find_target_plan_problems(collision_var, tm))


def test_s4_candidates_direct_et_single_enveloppe_refuses():
    tm = _manager()
    direct = _plan(_step("s1", "scan", {"query": "x"}))
    assert any("candidates" in hit and "directement" in hit
               for hit in find_target_plan_problems(direct, tm))

    wrapped = _plan(_step(
        "s1", "perceive_understand", {"question": "q", "source_tool": "focus"}
    ))
    assert any("single" in hit and "enveloppe" in hit
               for hit in find_target_plan_problems(wrapped, tm))


def test_s4_candidates_enveloppe_produit_un_alias_reutilisable():
    tm = _manager()
    plan = _plan(
        _step(
            "s1", "perceive_understand",
            {"question": "q", "source_tool": "scan"},
            output_target_name="btn",
        ),
        _step("s2", "click", {"target": "$@_btn"}),
    )
    assert find_target_plan_problems(plan, tm) == []


def test_s4_litteral_consommateur_refuse_sauf_raw_allow():
    tm = _manager()
    literal = _plan(_step("s1", "click", {"target": "bouton"}))
    assert any("nom local de cible" in hit for hit in find_target_plan_problems(literal, tm))

    raw = _plan(_step("s1", "click_raw", {"target": "ref-123"}))
    assert find_target_plan_problems(raw, tm) == []


def test_s4_t31_prompts_cibles_autorises_et_sans_fuite():
    tm = _manager()
    flags = target_prompt_flags(types.SimpleNamespace(tools_manager=tm), [])
    assert flags["targets_enabled"] is True
    assert flags["target_selection_enabled"] is True

    loader = get_prompt_loader()
    for lang in ("base", "en", "fr"):
        context = _prompt_context(
            variable_registry={
                "btn": {"type": "target", "description": "Fermer"},
            },
            registry="- `$@_btn` (target) : Fermer",
            targets_enabled=True,
            target_selection_enabled=True,
        )
        planner = loader.load("planner.md", lang=lang, **context)
        assert "(target)" in planner
        assert "output_target_name" in planner
        assert "ref-" not in planner
        assert "correlation_id" not in planner
        assert "generation" not in planner

        feasibility = loader.load("feasibility.md", lang=lang, **context)
        assert "(target)" in feasibility
        assert "ref-" not in feasibility

        grammar = loader.load(
            "plan_grammar.md", lang=lang, targets_enabled=True
        )
        assert "output_target_name" in grammar
        assert "$@_<" in grammar

        off = loader.load("plan_grammar.md", lang=lang, targets_enabled=False)
        assert "output_target_name" not in off
        assert "(target)" not in off


def test_s4_descriptions_des_consommateurs_seulement():
    tm = _manager()
    view = asyncio.run(tm.get_tools_view())
    click = next(tool for tool in view if tool["name"] == "click")
    properties = click["parameters"]["properties"]
    assert "[target]" in properties["target"]["description"]
    assert "[target]" not in properties["note"].get("description", "")
    original = tm._tools["click"]["parameters"]["properties"]
    assert "[target]" not in original["target"].get("description", "")


def test_s4_t32_contrat_invalide_reste_hors_surface():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        "click", "r", "click", _schema("target"),
        target_contract={"version": 1, "consumes": [{"param": "source"}]},
    )
    flags = target_prompt_flags(types.SimpleNamespace(tools_manager=tm), [])
    assert flags["targets_enabled"] is False
    assert flags["target_selection_enabled"] is False

    out = get_prompt_loader().load(
        "planner.md", lang="base", **_prompt_context()
    )
    assert "target_contract" not in out
    assert "output_target_name" not in out
    assert "(target)" not in out


def test_s4_t33_registre_ne_garde_pas_les_cibles_mortes():
    registry = TargetRegistry()
    live = registry.upsert(scope="ui_element", ref="ref-live", description="Live")
    dead = registry.upsert(scope="ui_element", ref="ref-dead", description="Dead")
    registry.invalidate(ref="ref-dead")

    view = registry.snapshot_view()
    assert live.local_name in view
    assert dead.local_name not in view
    assert view[live.local_name]["type"] == "target"


def test_s4_t34_selection_et_targets_sont_independants():
    tm_candidates = _manager()
    flags_candidates = target_prompt_flags(
        types.SimpleNamespace(tools_manager=tm_candidates), []
    )
    assert flags_candidates["targets_enabled"] is True
    assert flags_candidates["target_selection_enabled"] is True

    tm_single = ToolsManager(runtime_state=None)
    tm_single.register_tool(
        "focus", "r", "focus", _schema("query"),
        target_contract=SINGLE,
    )
    flags_single = target_prompt_flags(
        types.SimpleNamespace(tools_manager=tm_single), []
    )
    assert flags_single["targets_enabled"] is True
    assert flags_single["target_selection_enabled"] is False

    flags_off = target_prompt_flags(None, [])
    assert flags_off["targets_enabled"] is False
    assert flags_off["target_selection_enabled"] is False

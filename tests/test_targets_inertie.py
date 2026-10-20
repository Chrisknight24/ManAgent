"""S0 : inertie sans contrat, flags de prompts et fallback conservateur."""

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.prompt_loader as prompt_loader_module
from core.plan_models import PlanStep, StepType
from core.prompt_capabilities import (
    CAP_DISCOVERY,
    CAP_SKILLS,
    CAP_TARGETS,
    CAP_TYPED_ASSETS,
    CAP_WORLD,
    prompt_capabilities,
    target_prompt_flags,
)
from core.prompt_loader import PromptLoader, _UnsafeConditional, get_prompt_loader
from core.runtime_state import RuntimeState
from core.targets import TargetRegistry
from tools.tools_manager import ToolsManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_BASE = os.path.join(PROJECT_ROOT, "prompts", "base")

TARGET_TOKENS = (
    "target_contract",
    "output_target_name",
    "TARGET_",
    "dispatched",
    "(target)",
)

CONTRACT = {
    "version": 1,
    "consumes": [{"param": "target", "scope": "ui_element"}],
    "produces": [
        {
            "path": "data.targets",
            "mode": "candidates",
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }
    ],
}

SCHEMA = {
    "type": "object",
    "properties": {"target": {"type": "string"}},
    "required": ["target"],
}


def _manager(target_contract=None):
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="click",
        role="r",
        description="d",
        parameters_schema=SCHEMA,
        target_contract=target_contract,
    )
    return tm


def _prompt_context():
    return {
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
    }


def test_valid_contract_is_registered_outside_the_llm_view():
    tm = _manager(CONTRACT)
    assert tm.has_target_contracts()
    assert tm.get_target_contract("click") is not None
    assert "target_contract" not in tm._tools["click"]
    view = asyncio.run(tm.get_tools_view())
    assert all("target_contract" not in tool for tool in view)


def test_invalid_contract_keeps_the_tool_but_stays_inert():
    tm = _manager({"version": 1, "consumes": [{"param": "source"}]})
    assert tm.get_target_contract("click") is None
    assert not tm.has_target_contracts()
    assert "click" in tm._tools


def test_no_contract_means_no_target_capability():
    tm = _manager(None)
    caps = prompt_capabilities(types.SimpleNamespace(tools_manager=tm), [])
    assert CAP_TARGETS not in caps
    registry = TargetRegistry()
    assert registry.snapshot_view() == {}


def test_targets_and_world_are_independent():
    tm = _manager(CONTRACT)
    rs = types.SimpleNamespace(tools_manager=tm)
    deterministic = [{
        "name": "click",
        "source": "external",
        "kind": "action",
        "effects": "deterministic",
    }]
    caps = prompt_capabilities(rs, deterministic)
    assert CAP_TARGETS in caps
    assert CAP_WORLD not in caps

    tm2 = _manager(None)
    perception = [{"name": "vision", "source": "external", "kind": "perception"}]
    caps2 = prompt_capabilities(types.SimpleNamespace(tools_manager=tm2), perception)
    assert CAP_TARGETS not in caps2
    assert CAP_WORLD in caps2


def test_other_capabilities_are_opt_in():
    tools = [{"name": "x", "returns": [{"field": "out", "asset": "text/plain"}]}]
    caps = prompt_capabilities(None, tools, skills_text="s", discovery=True)
    assert CAP_TYPED_ASSETS in caps
    assert CAP_SKILLS in caps
    assert CAP_DISCOVERY in caps
    assert CAP_TARGETS not in caps


def test_render_flags_use_one_calculation():
    tm = _manager(CONTRACT)
    flags = target_prompt_flags(types.SimpleNamespace(tools_manager=tm), [])
    assert flags["targets_enabled"] is True
    assert flags["world_guidance"] is True
    assert target_prompt_flags(None, [])["targets_enabled"] is False


def test_runtime_state_owns_and_purges_a_registry():
    state = RuntimeState()
    try:
        entry = state.target_registry.upsert(scope="ui", ref="r1")
        assert state.target_registry.get(entry.local_name) is not None
        state.reset_target_registry()
        assert state.target_registry.snapshot_view() == {}
    finally:
        state.embedding_manager = None


def test_output_target_name_is_accepted_but_not_normalized():
    step = PlanStep(
        id="s1",
        description="d",
        type=StepType.TOOL_CALL,
        expected_result="true",
        tool_name="click",
        tool_args_json="{}",
        output_target_name="Ma_Cible",
    )
    assert step.output_target_name == "Ma_Cible"


def test_all_prompts_have_no_target_vocabulary_and_are_stable():
    loader = get_prompt_loader()
    context = _prompt_context()
    names = sorted(
        name for name in os.listdir(PROMPTS_BASE) if name.endswith(".md")
    )
    assert names
    for name in names:
        for lang in ("base", "en", "fr"):
            first = loader.load(name, lang=lang, **context)
            second = loader.load(name, lang=lang, **context)
            assert first == second, (name, lang)
            assert "{{" not in first and "{%" not in first, (name, lang)
            for token in TARGET_TOKENS:
                assert token not in first, (name, lang, token)


def test_fallback_drops_false_conditional_and_keeps_true_branch(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    template = base / "t.md"
    template.write_text(
        "A{% if secret %}SECRET{% endif %}B{{ name }}",
        encoding="utf-8",
    )
    loader = PromptLoader(base_dir=str(tmp_path), default_lang="base")
    assert loader._fallback_render("t.md", "base", {"secret": False, "name": "X"}) == "ABX"
    assert loader._fallback_render("t.md", "base", {"secret": True, "name": "X"}) == "ASECRETBX"


def test_fallback_preserves_existing_world_default_and_refuses_unknown_blocks(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    template = base / "t.md"
    template.write_text(
        "A{% if world_guidance is not defined or world_guidance %}W{% endif %}B",
        encoding="utf-8",
    )
    loader = PromptLoader(base_dir=str(tmp_path), default_lang="base")
    assert "W" in loader._fallback_render("t.md", "base", {})
    assert "W" not in loader._fallback_render("t.md", "base", {"world_guidance": False})

    template.write_text(
        "A{% for x in xs %}SECRET{% endfor %}B",
        encoding="utf-8",
    )
    with pytest.raises(_UnsafeConditional):
        loader._fallback_render("t.md", "base", {"xs": ["a"]})


def test_load_without_jinja_never_leaks_conditional_content(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    (base / "t.md").write_text(
        "A{% for x in xs %}SECRET{% endfor %}B",
        encoding="utf-8",
    )
    loader = PromptLoader(base_dir=str(tmp_path), default_lang="base")
    monkeypatch.setattr(prompt_loader_module, "JINJA2_AVAILABLE", False)
    result = loader.load("t.md", lang="base", xs=["a"])
    assert "SECRET" not in result
    assert result == "Prompt template 't.md'"

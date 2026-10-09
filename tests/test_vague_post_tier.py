"""Vague post-Tier : timeout, champs plan, arbitrage, compteurs (rapide, sans LLM)."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import PlanStep, StepType
from tools.tools_manager import ToolsManager
from core.alignment import note_truncation


def _step(**kwargs):
    base = dict(id="s1", description="d", type=StepType.TOOL_CALL,
                tool_name="mouse", tool_args_json="{}",
                expected_result="true")
    base.update(kwargs)
    return PlanStep(**base)


def test_champs_defaut_inertes():
    s = _step()
    assert s.should_world_state_change_after_action is None
    assert s.verify_with is None
    s2 = _step(should_world_state_change_after_action=True, verify_with="vision")
    assert s2.should_world_state_change_after_action is True
    assert s2.verify_with == "vision"


def test_timeout_parse():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(name="slow", role="r", description="d",
                     parameters_schema={}, kind="action", effects_timeout_ms=2500)
    assert tm.get_tool_timeout_ms("slow") == 2500
    tm.register_tool(name="bad", role="r", description="d",
                     parameters_schema={}, kind="action", effects_timeout_ms="nawak")
    assert tm.get_tool_timeout_ms("bad") is None
    tm.register_tool(name="neg", role="r", description="d",
                     parameters_schema={}, kind="action", effects_timeout_ms=-5)
    assert tm.get_tool_timeout_ms("neg") is None
    assert tm.get_tool_timeout_ms("inconnu") is None


def test_arbitrage_skip_sans_source():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(runtime_state=types.SimpleNamespace(
        tools_manager=ToolsManager(runtime_state=None)))
    step = _step()
    out = asyncio.run(ex._arbitrate_no_change(step))
    assert out is None


def test_compteurs_par_tag():
    rs = types.SimpleNamespace()
    assert note_truncation(rs, "Plan", "EOF while parsing x") == 1
    assert note_truncation(rs, "Plan", "EOF while parsing y") == 2
    assert note_truncation(rs, "Presentator_output", "EOF while parsing z") == 3
    assert note_truncation(rs, "Plan", "missing field") == 3
    assert rs.truncation_count == {"Plan": 2, "Presentator_output": 1}


def test_grammaire_changement_etat_3_langues():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "fr", "en"):
        text = open(os.path.join(base, "prompts", lang, "plan_grammar.md"),
                    encoding="utf-8").read()
        assert "should_world_state_change_after_action" in text, lang
        assert "verify_with" in text, lang


def test_phrase_subtile_3_langues():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en", "fr"):
        out = get_prompt_loader().load(
            "planner.md", lang=lang, goal="g", strategy="s", context="c",
            advice="", variable_registry={}, tools=[], skills="",
            model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "{{" not in out
        assert "verify_with" in out, lang

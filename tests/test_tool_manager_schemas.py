"""tool_manager montre les schémas hôte + pré-validation avant appel (rapide, sans LLM)."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_manager import ToolsManager
from tools.internal_tools import perceive_action, perceive_understand, _prevalidate_external


def _mgr():
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(name="vision", role="r", description="d",
                     parameters_schema={"type": "object",
                                        "properties": {"mode": {"type": "string"}},
                                        "required": ["mode"]},
                     kind="perception")
    tm.register_tool(name="mouse", role="r", description="d",
                     parameters_schema={"type": "object",
                                        "properties": {"action": {"type": "string"}},
                                        "required": ["action"]},
                     kind="action")
    return tm


def _rs(tm):
    return types.SimpleNamespace(tools_manager=tm)


def test_description_externe_montre_requis():
    desc = _mgr()._get_external_tools_description()
    assert "vision" in desc and "mode" in desc
    assert "mouse" in desc


def test_prevalidate_ok_et_ko():
    tm = _mgr()
    assert _prevalidate_external(tm, "vision", {"mode": "image"}) is None
    bad = _prevalidate_external(tm, "vision", {"query": "x"})
    assert bad is not None and "mode" in bad
    assert _prevalidate_external(tm, "outil_inconnu", {}) is None


def test_perceive_action_refuse_avant_appel_hote():
    tm = _mgr()

    async def _boom(name, args):
        raise AssertionError("l'hôte ne doit pas être appelé")

    tm.execute_tool = _boom
    out = asyncio.run(perceive_action(
        {"question": "q ?", "source_tool": "vision",
         "source_args": {"query": "bureau"},
         "action_tool": "mouse", "action_args": {"action": "click"}}, _rs(tm)))
    assert out["result"] is False
    assert "mode" in (out.get("error_reason") or "")


def test_perceive_action_args_action_verifies_aussi():
    tm = _mgr()

    async def _boom(name, args):
        raise AssertionError("l'hôte ne doit pas être appelé")

    tm.execute_tool = _boom
    out = asyncio.run(perceive_action(
        {"question": "q ?", "source_tool": "vision",
         "source_args": {"mode": "image"},
         "action_tool": "mouse", "action_args": {}}, _rs(tm)))
    assert out["result"] is False
    assert "action" in (out.get("error_reason") or "")


def test_perceive_understand_refuse_avant_appel_hote():
    tm = _mgr()

    async def _boom(name, args):
        raise AssertionError("l'hôte ne doit pas être appelé")

    tm.execute_tool = _boom
    out = asyncio.run(perceive_understand(
        {"question": "q ?", "source_tool": "vision", "source_args": {}}, _rs(tm)))
    assert out["result"] is False
    assert "mode" in (out.get("error_reason") or "")


def test_prompt_montre_schemas_hote():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en", "fr"):
        out = get_prompt_loader().load(
            "tools_manager_analysis.md", lang=lang, request="r",
            context="c", internal_tools_description="i",
            external_tools_description="- **vision** [perception] : requis=['mode']",
            registry_metadata="m")
        assert "{{" not in out and "vision" in out and "mode" in out

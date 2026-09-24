"""Tests kinds + méta-outil perceive_understand (rapides, sans LLM réel)."""
import sys
import os
import asyncio

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_manager import ToolsManager
from tools import internal_tools as IT
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_direct_perception_calls


def _tool_step(tool_name, sid="s1", args="{}"):
    return PlanStep(id=sid, description="d", type=StepType.TOOL_CALL,
                    tool_name=tool_name, tool_args_json=args,
                    expected_result="true")


def test_kind_defaults_to_action():
    tm = ToolsManager()
    tm.register_tool(name="x", role="r", description="d", parameters_schema={})
    assert tm._tools["x"]["kind"] == "action"
    assert "x" not in tm.perception_tool_names()


def test_manifest_kind_forwarded():
    tm = ToolsManager()
    tm.load_tools_from_payload([
        {"name": "lidar_scan", "kind": "perception"},
        {"name": "move_arm", "kind": "action"},
        {"name": "vieux_sans_kind"},
    ])
    assert tm.perception_tool_names() == {"lidar_scan"}
    assert "vieux_sans_kind" not in tm.perception_tool_names()


def test_internal_view_shows_kinds_and_meta_tool():
    tm = ToolsManager()
    view = {t["name"]: t for t in tm._get_internal_tools_view()}
    assert view["perceive_understand"]["kind"] == "perception"
    assert view["execute_skill"]["kind"] == "action"
    assert "perceive_understand" in tm.known_tool_names()


def test_gate_direct_perception():
    plan = Plan(goal="g", steps=[
        _tool_step("lidar_scan", "s1"),
        _tool_step("perceive_understand", "s2"),
        _tool_step("move_arm", "s3"),
    ])
    assert find_direct_perception_calls(plan, {"lidar_scan"}) == ["s1"]
    assert find_direct_perception_calls(plan, None) == []


class _FakeMgr:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def execute_tool(self, name, args, llm=None):
        import json
        self.calls.append((name, args))
        return json.dumps(self._payload)


def _fake_state(payload):
    from types import SimpleNamespace
    return SimpleNamespace(tools_manager=_FakeMgr(payload))


def test_meta_tool_happy_path(monkeypatch):
    seen = {}

    async def fake_run(data, query, runtime_state, tag="", media_assets=None):
        seen["q"] = query
        return {"result": True, "data": {"direction": "devant"}, "error_reason": None}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_run)
    st = _fake_state({"result": True, "data": {"scan": [1, 2]}})
    out = asyncio.run(IT.perceive_understand(
        {"question": "où est la porte ?",
         "source_tool": "lidar_scan", "source_args": {"angle": 360},
         "format_response": "direction et distance"},
        st))
    assert out["result"] is True
    assert "direction et distance" in seen["q"]
    assert st.tools_manager.calls[0][0] == "lidar_scan"


def test_meta_tool_refuses_self_and_missing():
    st = _fake_state({"result": True})
    out = asyncio.run(IT.perceive_understand(
        {"question": "x", "source_tool": "perceive_understand"}, st))
    assert out["result"] is False
    out2 = asyncio.run(IT.perceive_understand({}, st))
    assert out2["result"] is False

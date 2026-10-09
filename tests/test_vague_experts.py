"""Vague experts : regex tirets + snapshot convention + refresh (rapide, sans LLM)."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, is_simple_data_condition
from core.planner import Planner


def _plan_step(args_json):
    return PlanStep(
        id="step_9", description="lire", type=StepType.TOOL_CALL,
        tool_name="perceive_understand", tool_args_json=args_json,
        expected_result="true",
    )


def test_collecte_garde_nom_dashed_m4():
    planner = Planner.__new__(Planner)
    plan = Plan(goal="g", steps=[
        _plan_step('{"question": "q ?", "source_data": "$@_data_step_4-00020_image_base64"}'),
    ])
    registry = {"data_step_4-00020_image_base64": {"value": "x", "description": "d"}}
    ok, problems = planner._validate_plan(plan, registry)
    assert ok is True, problems
    assert problems == []


def test_condition_simple_dashed_ok():
    assert is_simple_data_condition('$@_data_step_4-00020 == "yes"') is True


def _rs(tools=None, manifest=None):
    class FakeTM:
        def __init__(self, tools):
            self._tools = tools or {}

        def known_tool_names(self):
            return set(self._tools)

        async def execute_tool(self, name, args):
            if name == "my_state":
                return '{"result": true, "data": "foreground: Paint"}'
            if name == "get_world_state":
                return '{"result": true, "data": "foreground: Calc"}'
            return '{"result": false, "data": null}'

    return types.SimpleNamespace(tools_manager=FakeTM(tools), host_manifest=manifest)


def test_snapshot_manifeste_prioritaire():
    from core.alignment import fetch_world_state_snapshot

    manifest = types.SimpleNamespace(
        metadata={"world_snapshot": {"source_tool": "my_state", "source_args": {}}},
        environment={})
    rs = _rs(tools={"my_state": {}, "get_world_state": {}}, manifest=manifest)
    out = asyncio.run(fetch_world_state_snapshot(rs))
    assert "Paint" in out


def test_snapshot_convention_sinon():
    from core.alignment import fetch_world_state_snapshot
    rs = _rs(tools={"get_world_state": {}})
    out = asyncio.run(fetch_world_state_snapshot(rs))
    assert "Calc" in out


def test_snapshot_skip_sans_rien():
    from core.alignment import fetch_world_state_snapshot
    rs = _rs(tools={"vision": {}})
    assert asyncio.run(fetch_world_state_snapshot(rs)) == ""


def test_snapshot_capé():
    from core.alignment import fetch_world_state_snapshot

    class FakeTM2:
        def known_tool_names(self):
            return {"get_world_state"}

        async def execute_tool(self, name, args):
            return '{"result": true, "data": "' + "y" * 5000 + '"}'

    rs = types.SimpleNamespace(tools_manager=FakeTM2(), host_manifest=None)
    out = asyncio.run(fetch_world_state_snapshot(rs, max_chars=100))
    assert out != "" and len(out) <= 120


def test_templates_snapshot_rendus():
    from core.prompt_loader import get_prompt_loader
    for name, kwargs in (
        ("feasibility.md", dict(goal="g", context="c", tools="t", tools_guidance="",
                                skills="", similar_missions="", registry="", advice="")),
        ("planner.md", dict(goal="g", strategy="s", context="c", advice="",
                            variable_registry={}, tools=[], skills="", model_id="m",
                            supported_modalities=[], unsupported_modalities=[])),
        ("convergence.md", dict(step_description="d", expected_result="e",
                                actual_result="r", tool_status="OK")),
    ):
        for lang in ("base", "en", "fr"):
            plain = get_prompt_loader().load(name, lang=lang, **kwargs)
            assert "{{" not in plain
            full = get_prompt_loader().load(
                name, lang=lang, world_snapshot="fenetre: Paint", **kwargs)
            assert "Paint" in full

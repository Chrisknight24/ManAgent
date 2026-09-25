"""Tests monde vivant : pattern explorer, exposition verrouillée, pas de systématique."""
import sys
import os
import asyncio
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discovery.providers.world_provider import WorldProvider
from core.discovery.explorers.world_explorer import WorldExplorer, GOALS
from tools import internal_tools as IT


def test_provider_declares_world_without_inventory():
    p = WorldProvider()
    assert p.get_data_type() == "world"
    assert p.get_targets() == []
    assert "monde" in p.get_scope_description().lower() or "world" in p.get_scope_description().lower() or len(p.get_scope_description()) > 10


def _explorer(ns_manager):
    from types import SimpleNamespace
    exp = WorldExplorer.__new__(WorldExplorer)
    exp._data_type = "world"
    exp.runtime_state = SimpleNamespace(tools_manager=ns_manager)
    exp.llm = None
    return exp


def test_sense_executes_perceive_understand(monkeypatch):
    from types import SimpleNamespace
    calls = {}

    async def fake_pu(args, runtime_state):
        calls.update(args)
        return {"result": True, "data": {"direction": "devant"}, "error_reason": None}

    monkeypatch.setattr(IT, "perceive_understand", fake_pu)

    class FakeMgr:
        def perception_tool_names(self):
            return {"lidar_scan"}

    exp = WorldExplorer.__new__(WorldExplorer)
    exp.runtime_state = SimpleNamespace(tools_manager=FakeMgr())
    exp.llm = None
    out = asyncio.run(exp.execute_tool("sense", {"question": "où est la porte ?"}))
    assert out["result"] is True
    assert calls["source_tool"] == "lidar_scan"


def test_sense_no_source_declared():
    from types import SimpleNamespace

    class FakeMgr:
        def perception_tool_names(self):
            return set()

    exp = _explorer(FakeMgr())
    out = asyncio.run(exp.execute_tool("sense", {"question": "x"}))
    assert out["result"] is False


def test_sense_unknown_tool_rejected():
    exp = _explorer(None)
    out = asyncio.run(exp.execute_tool("nope", {}))
    assert out["result"] is False


def test_planner_world_pd_off_by_default():
    from core.planner import Planner
    sig = inspect.signature(Planner.propose_plan)
    assert sig.parameters["enable_world_pd"].default is False


def test_planner_constructs_without_world_by_default():
    # Non-régression : construire un Planner ne doit jamais crasher
    # (a attrapé le NameError enable_world_pd).
    from types import SimpleNamespace
    from core.planner import Planner
    llm = SimpleNamespace(requirement=SimpleNamespace(role_name="general"),
                          provider_id="x", model_id="y", _discovery_enabled=True)
    rs = SimpleNamespace(discovery_engine=None, language="en")
    p = Planner.__new__(Planner)
    # __init__ réel avec fakes légers
    Planner.__init__(p, name="t", llm=llm, runtime_state=rs)
    assert "world" not in p.get_data_providers()
    # Seul le solver enregistre le provider "world" ; ni l'orchestrateur
    # (décisions directes) ni le presentator ne le voient.
    import ast
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("core/orchestrator.py", "core/presentator.py", "core/learner.py"):
        src = open(os.path.join(root, *rel.split("/")), encoding="utf-8").read()
        assert 'register_data_provider("world"' not in src, rel
        assert "register_data_provider('world'" not in src, rel
    src_solver = open(os.path.join(root, "core", "solver.py"), encoding="utf-8").read()
    assert 'register_data_provider("world"' in src_solver

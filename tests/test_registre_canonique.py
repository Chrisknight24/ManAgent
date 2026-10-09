"""Registre canonique + PD restreinte + snapshot (rapide, sans LLM)."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor


def _ex():
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(variable_registry={
        "bool_step_1-00063": {"value": "true", "description": "d", "source": "s"},
        "data_step_1-00063": {"value": "hello", "description": "d", "source": "s"},
        "data_paint_ouvert": {"value": "yes", "description": "d", "source": "s"},
    })
    return ex


def test_alias_base_resolus_sans_doublons():
    ex = _ex()
    key, entry = ex._find_var_in_registry("bool_step_1")
    assert entry is not None and entry["value"] == "true"
    key, entry = ex._find_var_in_registry("data_step_1")
    assert entry is not None and entry["value"] == "hello"
    key, entry = ex._find_var_in_registry("paint_ouvert")
    assert entry is not None and entry["value"] == "yes"
    key, entry = ex._find_var_in_registry("data_paint_ouvert")
    assert entry is not None


def test_solver_pd_sans_missions_ni_history():
    from core.solver import Solver

    class Parent:
        def get_data_providers(self):
            return {"missions": 1, "history": 2, "files": 3, "registry": 4,
                    "world": 5, "facts": 6, "inputs": 7, "outputs": 8}

    solver = Solver.__new__(Solver)
    solver.parent = Parent()
    solver._data_providers = {}
    providers = Solver.get_data_providers(solver)
    assert "missions" not in providers and "history" not in providers
    for keep in ("files", "registry", "world", "facts", "inputs", "outputs"):
        assert keep in providers, keep


def test_snapshot_vide_sans_perception():
    from core.solver import Solver

    class FakeTM:
        def perception_tool_names(self):
            return set()

    solver = Solver.__new__(Solver)
    solver.id = "s"
    solver.runtime_state = types.SimpleNamespace(
        tools_manager=FakeTM(), host_manifest=None, language="fr", llm=None)
    out = asyncio.run(solver._snapshot_world_default())
    assert out == ""


def test_snapshot_capé_et_fail_open():
    from core.solver import Solver

    class FakeTM:
        def perception_tool_names(self):
            return {"vision"}

    class FakeExplorer:
        def __init__(self, *a, **k):
            pass

        async def execute_tool(self, name, args):
            return {"result": True, "data": "x" * 5000}

    import core.discovery.explorers.world_explorer as we
    orig = we.WorldExplorer
    we.WorldExplorer = FakeExplorer
    try:
        solver = Solver.__new__(Solver)
        solver.id = "s"
        solver.llm = None
        solver.runtime_state = types.SimpleNamespace(
            tools_manager=FakeTM(), host_manifest=None, language="en")
        out = asyncio.run(solver._snapshot_world_default(max_chars=100))
        assert out != "" and len(out) <= 120
    finally:
        we.WorldExplorer = orig

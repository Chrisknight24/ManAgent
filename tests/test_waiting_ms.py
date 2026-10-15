"""D3 waiting_ms : résolution, modèle, compteur (rapide, sans LLM)."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import resolve_wait_ms
from core.plan_models import PlanStep, StepType


def test_resolve_planner_prime_meme_zero():
    assert resolve_wait_ms(5000, 1000, 3000) == (5000, "plan")
    assert resolve_wait_ms(0, 1000, 3000) == (0, "plan")


def test_resolve_hote_puis_defaut():
    assert resolve_wait_ms(None, 1000, 3000) == (1000, "hote")
    assert resolve_wait_ms(None, None, 3000) == (3000, "defaut")
    assert resolve_wait_ms(None, None, None) == (0, "aucune")


def test_resolve_plafond_et_negatif():
    assert resolve_wait_ms(99999, None, 3000) == (30000, "plan")
    assert resolve_wait_ms(None, 99999, 3000) == (30000, "hote")


def test_modele_accepte_absent_et_refuse_hors_bornes():
    s = PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                 tool_name="keyboard", tool_args_json="{}",
                 expected_result="true")
    assert s.waiting_ms is None
    s2 = PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                  tool_name="keyboard", tool_args_json="{}",
                  expected_result="true", waiting_ms=5000)
    assert s2.waiting_ms == 5000
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                 tool_name="keyboard", tool_args_json="{}",
                 expected_result="true", waiting_ms=-5)
    with pytest.raises(ValidationError):
        PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                 tool_name="keyboard", tool_args_json="{}",
                 expected_result="true", waiting_ms=40000)


def test_snapshot_expose_compteur_attentes(monkeypatch):
    import asyncio
    import types
    from utils.logger import Logger
    from core.solver import Solver
    events = []
    monkeypatch.setattr(Logger, "event",
                        staticmethod(lambda event_type, **fields: events.append(
                            {"event": event_type, **fields})))
    solver = Solver.__new__(Solver)
    solver.id = "m-wait"

    async def _fake_propagate(name, payload):
        pass

    solver.propagate_event = _fake_propagate
    solver.runtime_state = types.SimpleNamespace(
        llm_usage={"total": {}, "by_mission": {}},
        truncation_count={},
        waiting_stats={"defaut": 2, "plan": 1},
    )
    asyncio.run(solver._emit_stats_snapshot())
    snap = next(e for e in events if e["event"] == "stats.snapshot")
    assert snap["waiting"] == {"defaut": 2, "plan": 1}


def _ex_wait():
    import asyncio as _aio  # noqa: F401
    from core.executor import Executor

    class FakeTM:
        def perception_tool_names(self):
            return {"vision", "get_world_state"}

        def get_tool_timeout_ms(self, name):
            return None

    solver = types.SimpleNamespace(
        id="s-wait", variable_registry={},
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=types.SimpleNamespace(
            cancel_requested=False, waiting_stats={},
            tools_manager=FakeTM()),
        propagate_event=None,
    )

    async def _fake_propagate(name, payload):
        calls.append((name, payload))

    calls = []
    solver.propagate_event = _fake_propagate
    ex = Executor.__new__(Executor)
    ex.solver = solver
    ex._applied_waits = []
    return ex, calls


def _wstep(tool, waiting_ms):
    from core.plan_models import PlanStep, StepType
    return PlanStep(id="step_9", description="d", type=StepType.TOOL_CALL,
                    tool_name=tool, tool_args_json="{}",
                    expected_result="true", waiting_ms=waiting_ms)


def test_placement_avant_lecture_apres_action():
    from core.executor import Executor
    ex, _ = _ex_wait()
    assert ex._wait_placement("vision") == "before"
    assert ex._wait_placement("perceive_understand") == "before"
    assert ex._wait_placement("keyboard") == "after"
    assert ex._wait_placement("mouse") == "after"


def test_pre_attente_lecture_seulement():
    import asyncio
    ex, calls = _ex_wait()
    ms = asyncio.run(ex._apply_step_wait(_wstep("vision", 50), "before"))
    assert ms == 50
    assert any(n == "pause.started" for n, _ in calls)
    assert any(n == "pause.finished" for n, _ in calls)
    ex2, calls2 = _ex_wait()
    ms2 = asyncio.run(ex2._apply_step_wait(_wstep("vision", 50), "after"))
    assert ms2 == 0 and calls2 == []
    ex3, calls3 = _ex_wait()
    ms3 = asyncio.run(ex3._apply_step_wait(_wstep("keyboard", 50), "before"))
    assert ms3 == 0 and calls3 == []


def test_full_mission_reprend_vrai_rapport(monkeypatch):
    import asyncio
    from utils.logger import Logger
    from core.solver import Solver
    events = []
    monkeypatch.setattr(Logger, "event",
                        staticmethod(lambda event_type, **fields: events.append(
                            {"event": event_type, **fields})))

    async def _fake_propagate(name, payload):
        events.append({"event": name, "packet": True, **payload})

    solver = Solver.__new__(Solver)
    solver.id = "m-full"
    solver.propagate_event = _fake_propagate
    asyncio.run(solver._emit_mission_convergence(
        "pass", "Mission accomplie.",
        full="Le résultat est 46, lu à l'écran."))
    pkt = next(e for e in events if e.get("packet"))
    assert pkt["detail"] == "Mission accomplie."
    assert pkt["full"] == "Le résultat est 46, lu à l'écran."

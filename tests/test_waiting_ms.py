"""D3 waiting_ms : résolution, modèle, compteur (rapide, sans LLM)."""
import os
import sys

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

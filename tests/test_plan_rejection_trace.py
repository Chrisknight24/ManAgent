"""Rejets avant-execution : event + plan stocke, visibles en HTML. Nos yeux auto."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.execution_models import PlanAttempt, FailureClass
from core.solver import Solver, _pydantic_feedback
from utils.logger import Logger


def _plan():
    return Plan(goal="g", steps=[
        PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                 tool_name="perceive", tool_args_json="{}",
                 expected_result="true"),
    ])


def _fake_solver(monkeypatch):
    events = []
    monkeypatch.setattr(Logger, "event",
                        staticmethod(lambda event_type, **fields: events.append(
                            {"event": event_type, **fields})))
    markers = {}
    solver = Solver.__new__(Solver)
    solver.id = "solver_test"

    class FakePlanner:
        _last_proposed_plan = _plan()

    class FakeRS:
        def update_marker(self, key, value):
            markers[key] = value

    solver.planner = FakePlanner()
    solver.runtime_state = FakeRS()
    solver.current_attempt = PlanAttempt(attempt_number=1, started_at=time.time(),
                                         outcome="in_progress",
                                         failure_class=FailureClass.NONE)
    return solver, events, markers


def test_value_error_path_emits_event_and_stores_plan(monkeypatch):
    solver, events, markers = _fake_solver(monkeypatch)
    Solver._record_plan_rejection(solver, "variable inconnue 'x'", 1, store_plan=True)
    assert markers.get("plan_rejected") is True
    kinds = [e["event"] for e in events]
    assert "plan_rejected_validation" in kinds
    ev = next(e for e in events if e["event"] == "plan_rejected_validation")
    assert ev["attempt"] == 1 and "variable inconnue" in ev["reason"]
    att = solver.current_attempt
    assert att.outcome == "failed"
    assert att.failure_class == FailureClass.PLAN_REJECTED_VALIDATION
    assert att.failure_reason == "variable inconnue 'x'"
    assert att.target_entity == "Planner"
    assert (att.proposed_plan or {}).get("steps", [{}])[0].get("id") == "step_1"


def test_pydantic_path_emits_event_without_stale_plan(monkeypatch):
    solver, events, _ = _fake_solver(monkeypatch)
    Solver._record_plan_rejection(solver, "Erreur de validation Pydantic : boom", 2,
                                  store_plan=False)
    assert any(e["event"] == "plan_rejected_validation" and e["attempt"] == 2
               for e in events)
    assert solver.current_attempt.proposed_plan is None
    assert solver.current_attempt.outcome == "failed"


def test_troncation_dit_plus_court():
    msg = _pydantic_feedback("1 validation error for Plan\n  Invalid JSON: EOF while parsing a string at line 82")
    assert "PLUS COURT" in msg
    assert "EOF" in msg


def test_autre_erreur_garde_dump():
    msg = _pydantic_feedback("missing field 'steps'")
    assert "PLUS COURT" not in msg
    assert "missing field" in msg


def test_is_truncation_error():
    from core.solver import _is_truncation_error
    assert _is_truncation_error("EOF while parsing a string") is True
    assert _is_truncation_error("Unterminated string") is True
    assert _is_truncation_error("missing field 'steps'") is False
    assert _is_truncation_error("") is False

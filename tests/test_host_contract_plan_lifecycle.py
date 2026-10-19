"""Contrat observabilité hôte (2026-10-19) — cycle de vie des plans déterministe.

4 ajouts additifs : plan_id, plan.proposed avant validation, reason réelle +
failed_step_id sur abandon, supersedes sur plan.generated.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import Events
from core.execution_models import PlanAttempt
from core.executor import Executor
from core.id_generator import make_plan_id
from core.solver import Solver


def test_make_plan_id_format_et_unicite():
    a = make_plan_id()
    b = make_plan_id()
    assert a.startswith("plan-") and b.startswith("plan-")
    assert a != b


def test_event_plan_proposed():
    assert Events.PLAN_PROPOSED == "plan.proposed"


def test_plan_attempt_porte_plan_id():
    att = PlanAttempt(attempt_number=1, plan_id="plan-42")
    assert att.plan_id == "plan-42"
    assert att.model_dump()["plan_id"] == "plan-42"


def test_step_event_meta_echo_plan_id():
    ex = Executor.__new__(Executor)
    ex._current_plan_id = "plan-9"
    node = types.SimpleNamespace(
        started_at=None, ended_at=None, tool_name=None, tool_args=None)
    meta = ex._step_event_meta(node)
    assert meta["plan_id"] == "plan-9"


def test_step_event_meta_sans_plan_id():
    ex = Executor.__new__(Executor)
    node = types.SimpleNamespace(
        started_at=None, ended_at=None, tool_name=None, tool_args=None)
    assert "plan_id" not in ex._step_event_meta(node)


def test_validate_plan_transmet_plan_id():
    captured = {}

    async def parent_validate(plan, sid, previous_attempts=None,
                              mission_history_tree=None, plan_id=None):
        captured["plan_id"] = plan_id
        return True

    s = Solver.__new__(Solver)
    s.id = "child"
    s.execution_tree = None
    s.parent = types.SimpleNamespace(validate_plan=parent_validate)
    s._get_root_solver = lambda: None

    ok = asyncio.run(s.validate_plan(object(), "child", plan_id="plan-7"))
    assert ok is True
    assert captured["plan_id"] == "plan-7"


def test_last_failed_step_id():
    from core.plan_models import ExecutionStatus

    ok = types.SimpleNamespace(status=ExecutionStatus.SUCCESS, step_id="step_1")
    ko = types.SimpleNamespace(status=ExecutionStatus.FAILED, step_id="step_2")
    s = Solver.__new__(Solver)
    s.current_attempt = types.SimpleNamespace(nodes=[ok, ko])
    assert s._last_failed_step_id() == "step_2"
    s.current_attempt = types.SimpleNamespace(nodes=[ok])
    assert s._last_failed_step_id() is None

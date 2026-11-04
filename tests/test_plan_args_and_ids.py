"""F3 : gates déterministes sur les args illisibles et les ids dupliqués.

Mission `046a2a3b` (2026-10-31) : le plan contenait
`{"mode":"key"} formulation_args:{} waiting_ms:1000` et trois étapes nommées
`step_1`. Le harnais n'a signalé que « args illisibles étape:step_5 » (sans
le texte fautif), et n'a jamais vu les ids dupliqués — qui écrasent
silencieusement le registre de variables.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_duplicate_step_ids, find_malformed_step_args
from core.planner import Planner


def _step(step_id, args='{"mode": "key", "key": "enter"}'):
    return PlanStep(
        id=step_id,
        description="étape",
        type=StepType.TOOL_CALL,
        tool_name="keyboard",
        tool_args_json=args,
        expected_result="true",
    )


def test_malformed_args_report_the_offending_text():
    plan = Plan(goal="g", steps=[
        _step("step_1", '{"mode": "key"} waiting_ms:1000'),
    ])

    ok, errors = Planner._validate_plan(None, plan, {})

    assert not ok
    joined = "\n".join(errors)
    assert "args illisibles" in joined
    assert "waiting_ms:1000" in joined


def test_malformed_ids_are_deduplicated():
    plan = Plan(goal="g", steps=[
        _step("step_1", '{"mode": "key"} waiting_ms:1000'),
        _step("step_1", '{"mode": "key"} reader_ms:1000'),
    ])

    assert find_malformed_step_args(plan) == ["step_1"]


def test_duplicate_ids_are_detected():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_1"),
        _step("step_2"),
    ])

    assert find_duplicate_step_ids(plan) == ["step_1"]


def test_duplicate_ids_reject_the_plan():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_1"),
    ])

    ok, errors = Planner._validate_plan(None, plan, {})

    assert not ok
    assert any("step_1" in e and "dupliqu" in e.lower() for e in errors)


def test_unique_ids_produce_no_noise():
    plan = Plan(goal="g", steps=[_step("step_1"), _step("step_2")])

    assert find_duplicate_step_ids(plan) == []

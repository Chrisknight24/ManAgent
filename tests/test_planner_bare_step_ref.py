"""F2 : une seule règle de résolution entre Planner et Supervisor.

Deux divergences constatées sur les missions du 2026-10-31 :

1. `$@_step_7` nu était ACCEPTÉ par `Planner._validate_plan` (parce que
   `bool_step_7` existe) puis REFUSÉ par `find_unresolved_plan_refs`. Le
   modèle brûlait une tentative sur un plan que le Supervisor rejetait de
   toute façon.

2. La piste « Vouliez-vous dire » envoyait `$@_target_step_3` vers
   `$@_data_step_3` / `$@_data_step_2` — le mauvais canal (une cible n'est
   pas une donnée). Le message « Cibles non déclarées » suffit.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.planner import Planner


def _step(step_id, tool="keyboard", args='{"mode": "key", "key": "enter"}',
          output_target_name=None):
    return PlanStep(
        id=step_id,
        description="étape",
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json=args,
        output_target_name=output_target_name,
        expected_result="true",
    )


def test_bare_step_reference_is_rejected_like_the_supervisor_does():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_2"),
        _step("step_3", args='{"mode": "text", "text": "$@_step_2"}'),
    ])

    ok, errors = Planner._validate_plan(None, plan, {})

    assert not ok
    joined = "\n".join(errors)
    assert "$@_data_step_2" in joined
    assert "$@_bool_step_2" in joined


def test_prefixed_auto_names_stay_valid():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_2", args='{"mode": "text", "text": "$@_data_step_1"}'),
    ])

    ok, _ = Planner._validate_plan(None, plan, {})

    assert ok


def test_target_reference_does_not_get_a_data_channel_hint():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_2"),
        _step("step_3", tool="target", args='{"cell": "A1", "label": "navigateur"}'),
        _step("step_4", tool="mouse",
              args='{"action": "click", "target": "$@_target_step_3"}'),
    ])

    ok, errors = Planner._validate_plan(None, plan, {})

    assert not ok
    joined = "\n".join(errors)
    assert "output_target_name" in joined
    assert "Vouliez-vous dire" not in joined


def test_non_target_reference_keeps_the_suggestion():
    plan = Plan(goal="g", steps=[
        _step("step_1"),
        _step("step_2", args='{"mode": "text", "text": "$@_data_step_9"}'),
    ])

    ok, errors = Planner._validate_plan(None, plan, {})

    assert not ok
    joined = "\n".join(errors)
    assert "Vouliez-vous dire" in joined

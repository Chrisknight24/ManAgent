"""Message d'erreur cible : nommer `output_target_name`, pas `output_variable_name`.

Bug reel (mission `008bcdba`) : le consommateur `mouse` reference
`$@_target_rectangle`, jamais publie. Le rejet est correct, mais le message
suggere `output_variable_name` — qui ne publie PAS d'alias de cible. Le
planner corrige donc au mauvais endroit et brule une tentative.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_unresolved_plan_refs


def _consumer(tool="mouse", target="$@_target_rectangle"):
    return PlanStep(
        id="step_2",
        description="cliquer sur l'outil rectangle",
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json='{"action": "click", "target": "%s"}' % target,
        expected_result="true",
    )


def test_target_consumer_hint_names_output_target_name():
    plan = Plan(goal="g", steps=[_consumer()])
    hits = find_unresolved_plan_refs(plan, target_consumers={"mouse": {"target"}})
    assert len(hits) == 1
    assert "output_target_name" in hits[0]
    assert "output_variable_name" not in hits[0]


def test_non_target_consumer_keeps_the_historical_hint():
    plan = Plan(goal="g", steps=[_consumer(tool="write_file")])
    hits = find_unresolved_plan_refs(plan, target_consumers={"mouse": {"target"}})
    assert len(hits) == 1
    assert "output_variable_name" in hits[0]


def test_no_mapping_keeps_the_historical_hint():
    plan = Plan(goal="g", steps=[_consumer()])
    hits = find_unresolved_plan_refs(plan)
    assert len(hits) == 1
    assert "output_variable_name" in hits[0]

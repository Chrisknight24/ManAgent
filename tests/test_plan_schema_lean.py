"""Schéma de plan exposé au LLM : pas de champs purement internes.

`status` et `result_context` sont écrits par l'exécuteur, jamais par le
modèle. Les laisser dans le schéma coûte des tokens et invite le modèle à
inventer un statut. `tool_name` / `tool_args_json` / `response_text` restent
(lus par le LLM dans les replays), ainsi que les champs de contrat.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep


def _step_props():
    schema = Plan.model_json_schema()
    defs = schema.get("$defs") or schema.get("definitions") or {}
    step = defs.get("PlanStep") or {}
    return set((step.get("properties") or {}).keys())


def test_internal_fields_are_not_exposed_to_the_llm():
    props = _step_props()
    assert "status" not in props
    assert "result_context" not in props


def test_contract_fields_stay_exposed():
    props = _step_props()
    for name in ("id", "description", "type", "tool_name", "tool_args_json",
                 "response_text", "expected_result", "execute_if",
                 "output_variable_name", "output_target_name"):
        assert name in props, name


def test_model_still_has_internal_defaults():
    step = PlanStep(
        id="step_1",
        description="attendre",
        type="tool_call",
        tool_name="pause",
        tool_args_json="{}",
        expected_result="true",
    )
    assert step.status.value == "pending"
    assert step.result_context == ""

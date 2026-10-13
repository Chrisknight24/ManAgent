"""Vague convergence+juge Bloc B : rendu P2, monde juge, rejected vs failed."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, PlanValidationDecision
from core.execution_models import PlanAttempt, FailureClass, ExecutionTree
from core.plan_validator import PlanValidator


def _p2_step():
    return PlanStep(
        id="step_1", description="saisir calcul", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json='{"mode": "text", "text": "12+34="}',
        expected_result="true", step_context="Calculatrice au premier plan.",
        should_world_state_change_after_action=True, verify_with="get_world_state",
    )


def _plain_step():
    return PlanStep(
        id="step_1", description="ouvrir", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json="{}",
        expected_result="true",
    )


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def test_rendu_montre_p2_en_clair():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _plan(_p2_step()))
    assert "Calculatrice au premier plan" in text
    assert "monde=oui" in text
    assert "verif=get_world_state" in text


def test_rendu_cache_p2_nul():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _plan(_plain_step()))
    assert "monde=" not in text
    assert "verif=" not in text


def _attempt(num, outcome, failure_class, reason):
    return PlanAttempt(
        attempt_number=num, outcome=outcome, failure_class=failure_class,
        failure_reason=reason, proposed_plan=_plan(_plain_step()).model_dump(mode="json"),
        nodes=[],
    )


def _tree():
    tree = ExecutionTree(solver_id="s1", goal="g", depth=0, status="in_progress")
    tree.attempts.append(_attempt(1, "failed", FailureClass.EXECUTION_FAILURE, "outil false"))
    tree.attempts.append(_attempt(2, "rejected", FailureClass.PLAN_REJECTED_SUPERVISOR, "pas d'ouverture"))
    return tree


def test_historique_distingue_rejected():
    text = PlanValidator.summarize_mission_history(_tree())
    assert "[failed]" in text and "ÉCHEC" in text
    assert "[rejected]" in text and "REFUSÉ" in text
    assert "plan non exécuté" in text


def test_repetition_compte_rejected_avant_exec():
    plan = _plan(_plain_step())
    fact = PlanValidator.build_repetition_fact(plan, _tree().attempts)
    assert fact is not None
    assert "AVANT" in fact


def test_novelty_voit_rejected():
    from core.plan_validator import compare_plan_novelty
    plan = _plan(_plain_step())
    res = compare_plan_novelty(plan, _tree().attempts)
    assert res is not None


def _validator(calls):
    async def fake_gen(prompt, schema, tag=None, **kw):
        calls["prompt"] = prompt
        return PlanValidationDecision(is_conformant=True, reason="ok")
    from core.prompt_loader import PromptLoader
    return PlanValidator(
        llm=SimpleNamespace(generate_structured=fake_gen),
        prompt_loader=PromptLoader(),
        rules_text="regles",
        language="en",
        available_tools={"keyboard"},
        production_skills=set(),
        perception_tools=set(),
        tool_returns={},
        availability_summary="keyboard",
    )


def test_monde_injecte_au_juge():
    calls = {}
    v = _validator(calls)
    out = asyncio.run(v.validate(
        plan=_plan(_plain_step()), child_solver_id="s1", target_goal="g",
        previous_attempts=[], mission_history_tree=None,
        world_snapshot="Avant-plan : Calculatrice",
    ))
    assert bool(out) is True
    assert "Avant-plan : Calculatrice" in calls["prompt"]


def test_monde_absent_pas_de_bloc():
    calls = {}
    v = _validator(calls)
    asyncio.run(v.validate(
        plan=_plan(_plain_step()), child_solver_id="s1", target_goal="g",
        previous_attempts=[], mission_history_tree=None,
        world_snapshot="",
    ))
    assert "CURRENT WORLD STATE" not in calls["prompt"]

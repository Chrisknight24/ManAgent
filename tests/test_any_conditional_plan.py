"""Plans conditionnels : `any` accepte une valeur négative, `null` = `true` rigide.

Doctrine validée le 2026-10-29 :
- `any` = « je ne juge pas la valeur, je branche dessus » → un `result=false`
  est une VALEUR négative légitime, pas un échec de plan.
- absent / `null` = `"true"` rigide pour un `tool_call`.
- `"false"` reste un échec par défaut.
- Une sortie `any` non consommée est un AVERTISSEMENT, jamais un refus.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_unconsumed_any_outputs


def _ex():
    return Executor.__new__(Executor)


def _plan(*steps):
    return Plan(goal="test", steps=list(steps))


def _any_step(sid, out=None, tool="perceive_understand"):
    return PlanStep(
        id=sid,
        description="lire",
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json='{"question": "q", "source_tool": "vision"}',
        expected_result="any",
        output_variable_name=out,
    )


# --------------------------------------------------------------------------
# P0.1 — any accepte false
# --------------------------------------------------------------------------

def test_any_accepts_false_result():
    ok, why = _ex()._verify_rigid_outcome(
        "any", "non. On voit actuellement Qt Creator.", "Paint absent", "false"
    )
    assert ok is True
    assert "any" in why.lower() or "valeur" in why.lower()


def test_any_accepts_false_without_data():
    ok, _ = _ex()._verify_rigid_outcome("any", "false", "", "false")
    assert ok is True


def test_any_without_flag_and_error_text_rejected():
    # Pas de statut structuré + texte d'erreur = vrai échec non structuré.
    ok, _ = _ex()._verify_rigid_outcome("any", "Erreur : source inconnue", "", None)
    assert ok is False


def test_true_false_still_strict():
    ex = _ex()
    assert ex._verify_rigid_outcome("true", "true", "", "true")[0] is True
    assert ex._verify_rigid_outcome("true", "true", "", "false")[0] is False
    assert ex._verify_rigid_outcome("false", "false", "", "false")[0] is True
    assert ex._verify_rigid_outcome("false", "true", "", "true")[0] is False


def test_any_step_converges_with_false_flag():
    # C'est cette convergence qui, avant, abortait le plan entier.
    ex = _ex()
    step = SimpleNamespace(
        type=StepType.TOOL_CALL, expected_result="any", id="step_1",
        tool_name="perceive_understand",
    )
    node = SimpleNamespace(raw_success_flag="false")
    dec = asyncio.run(
        ex._check_convergence_inner(step, "non. On voit…", node=node)
    )
    assert dec.is_convergent is True


# --------------------------------------------------------------------------
# P0.2 — null / absent = true rigide
# --------------------------------------------------------------------------

def test_default_expected_result_is_none_in_schema():
    step = PlanStep(
        id="s1", description="d", type=StepType.TOOL_CALL,
        tool_name="x", tool_args_json="{}",
    )
    assert step.expected_result is None


def test_null_expected_is_rigid_true():
    ex = _ex()
    step = SimpleNamespace(
        type=StepType.TOOL_CALL, expected_result=None, id="s1",
        tool_name="get_world_state",
    )
    ok_node = SimpleNamespace(raw_success_flag="true")
    ko_node = SimpleNamespace(raw_success_flag="false")
    assert asyncio.run(ex._check_convergence_inner(step, "true", node=ok_node)).is_convergent is True
    assert asyncio.run(ex._check_convergence_inner(step, "false", node=ko_node)).is_convergent is False


def test_abstract_task_keeps_semantic_expected_result():
    ex = _ex()
    hits = []

    async def fake_sem(step, actual_result, tool_status=None):
        hits.append((step.expected_result, tool_status))
        from core.plan_models import ConvergenceDecision
        return ConvergenceDecision(is_convergent=False, reason="insuffisant")

    ex._evaluate_semantic_convergence = fake_sem
    step = SimpleNamespace(
        type=StepType.ABSTRACT_TASK, expected_result="le carré est visible", id="s1"
    )
    dec = asyncio.run(ex._check_convergence_inner(step, "result", action_success=True))
    assert hits == [("le carré est visible", "OK")]
    assert dec.is_convergent is False


# --------------------------------------------------------------------------
# P0.3 — un résultat négatif garde sa donnée
# --------------------------------------------------------------------------

def test_negative_result_keeps_data_in_trace():
    assert Executor._format_tool_output("false", "non. On voit…", "absent") == "non. On voit…"
    assert Executor._format_tool_output("false", {"a": 1}, "absent").strip().startswith("{")
    err = Executor._format_tool_output("false", None, "boom")
    assert err.startswith("Erreur")


# --------------------------------------------------------------------------
# P0.4 — warning any non consommé, jamais un refus
# --------------------------------------------------------------------------

def test_any_unconsumed_is_flagged():
    plan = _plan(
        _any_step("step_1", tool="llm_analyze_data"),
        PlanStep(id="step_2", description="dire", type=StepType.DIRECT_ANSWER,
                 expected_result="ok", response_text="fini"),
    )
    hits = find_unconsumed_any_outputs(plan)
    assert len(hits) == 1
    assert "step_1" in hits[0]
    assert "execute_if" in hits[0]


def test_any_consumed_by_execute_if_is_not_flagged():
    plan = _plan(
        _any_step("step_1"),
        PlanStep(id="step_2", description="ouvrir", type=StepType.TOOL_CALL,
                 tool_name="keyboard", tool_args_json="{}", expected_result="true",
                 execute_if='$@_bool_step_1 == false'),
    )
    assert find_unconsumed_any_outputs(plan) == []


def test_any_consumed_by_args_is_not_flagged():
    plan = _plan(
        _any_step("step_1"),
        PlanStep(id="step_2", description="lire", type=StepType.TOOL_CALL,
                 tool_name="llm_analyze_data",
                 tool_args_json='{"source": "$@_data_step_1"}',
                 expected_result="true"),
    )
    assert find_unconsumed_any_outputs(plan) == []


def test_any_perception_skipped_to_avoid_double_warning():
    # La perception a déjà son propre signal (find_unconsumed_perception_outputs).
    plan = _plan(_any_step("step_1"))
    assert find_unconsumed_any_outputs(plan, {"perceive_understand"}) == []


def test_convergence_template_renders_expected_warnings():
    from core.prompt_loader import get_prompt_loader
    out = get_prompt_loader().load(
        "convergence.md", lang="base",
        step_description="d", expected_result="any", actual_result="non",
        tool_status="UNKNOWN", expected_warnings="[AVERTISSEMENT] sortie any non lue",
    )
    assert "sortie any non lue" in out
    assert "jamais un échec" in out or "pas un échec" in out

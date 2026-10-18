"""BUG A (P0) — `waiting_ms` invisible pour le juge de plans.

Le résumé du plan montré au validator (`_summarize_plan_for_prompt`) omettait
`waiting_ms`. Le juge ne voyait donc pas l'attente déclarée et refusait à tort
un plan « sans attente », en inventant une exigence d'étape `wait` séparée —
alors que `waiting_ms` EST le mécanisme d'attente (défaut 3000 ms appliqué).

Cause racine identifiée de l'échec de la mission 1 (apollo/VLC).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import PlanValidator

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _step(sid, waiting_ms=None):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json='{"key": "SPACE"}',
        expected_result="true", waiting_ms=waiting_ms,
    )


def _summary(plan):
    v = PlanValidator.__new__(PlanValidator)
    return PlanValidator._summarize_plan_for_prompt(v, plan)


def test_waiting_ms_visible_dans_le_resume():
    text = _summary(Plan(goal="g", steps=[_step("step_1", waiting_ms=5000)]))
    assert "wait_ms=5000" in text


def test_absence_de_waiting_ms_n_affiche_rien():
    text = _summary(Plan(goal="g", steps=[_step("step_1")]))
    assert "wait_ms" not in text


def test_waiting_ms_zero_explicite_visible():
    text = _summary(Plan(goal="g", steps=[_step("step_1", waiting_ms=0)]))
    assert "wait_ms=0" in text


def _prompt_text(lang):
    path = os.path.join(_ROOT, "prompts", lang, "plan_validation.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_prompt_interdit_etape_wait_separee_en():
    txt = _prompt_text("en")
    assert "never demand a separate" in txt
    assert "wait_ms" in txt


def test_prompt_interdit_etape_wait_separee_fr():
    txt = _prompt_text("fr")
    assert "wait_ms" in txt
    assert "étape `wait` séparée" in txt

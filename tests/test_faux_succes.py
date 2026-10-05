"""Anti faux-succes : any ne valide plus un echec + source_tool agnostique (rapide, sans LLM)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_invalid_perceive_sources


def _ex():
    return Executor.__new__(Executor)


def _plan(*steps):
    return Plan(goal="test", steps=list(steps))


def _pu_step(sid, args, expected="any"):
    return PlanStep(
        id=sid, description="lire", type=StepType.TOOL_CALL,
        tool_name="perceive_understand",
        tool_args_json=json.dumps(args),
        expected_result=expected,
    )


def test_any_with_false_flag_rejected():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="any", actual="true",
        supplemental_data="Tool 'get_annotated_image' not recognized",
        raw_success_flag="false",
    )
    assert ok is False


def test_any_with_true_flag_accepted():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="any", actual="yes",
        supplemental_data="", raw_success_flag="true",
    )
    assert ok is True


def test_any_with_error_text_rejected():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="any", actual="Erreur : source inconnue",
        supplemental_data="", raw_success_flag=None,
    )
    assert ok is False


def test_true_false_still_strict():
    ex = _ex()
    ok, _ = ex._verify_rigid_outcome("true", "true", "", "true")
    assert ok is True
    ok, _ = ex._verify_rigid_outcome("true", "true", "", "false")
    assert ok is False


def test_unknown_source_flagged():
    plan = _plan(_pu_step("s1", {"question": "q ?", "source_tool": "get_annotated_image", "source_args": {}}))
    bad = find_invalid_perceive_sources(plan, {"keyboard", "vision", "perceive_understand"})
    assert bad == ["s1: source_tool=get_annotated_image"]


def test_known_source_passes():
    plan = _plan(_pu_step("s1", {"question": "q ?", "source_tool": "vision", "source_args": {}}))
    assert find_invalid_perceive_sources(plan, {"keyboard", "vision", "perceive_understand"}) == []


def test_source_data_needs_no_tool():
    plan = _plan(_pu_step("s1", {"question": "q ?", "source_data": "$@_data_capture"}))
    assert find_invalid_perceive_sources(plan, {"vision"}) == []


def test_no_referential_means_no_check():
    plan = _plan(_pu_step("s1", {"question": "q ?", "source_tool": "nimporte"}))
    assert find_invalid_perceive_sources(plan, None) == []


def test_planner_prompts_have_no_hardcoded_host_tool():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang_file in ("prompts/base/planner.md", "prompts/fr/planner.md", "prompts/en/planner.md"):
        text = open(os.path.join(base, lang_file), encoding="utf-8").read()
        assert "get_annotated_image" not in text, lang_file
        assert "get_image" not in text, lang_file


def test_plan_validation_prompt_leaves_syntax_to_code():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fr = open(os.path.join(base, "prompts/fr/plan_validation.md"), encoding="utf-8").read()
    en = open(os.path.join(base, "prompts/en/plan_validation.md"), encoding="utf-8").read()
    assert "$@_" in fr and "code" in fr.lower()
    assert "$@_" in en and "code" in en.lower()

"""A4b : une abstract_task passe TOUJOURS par une ConvergenceDecision, même
si `expected_result` est vide. Le bypass « vide = accepté » reste scopé aux
étapes non-abstract_task (tool_call)."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import StepType, ConvergenceDecision


def test_abstract_task_empty_expected_goes_semantic():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    hits = []

    async def fake_sem(step, actual_result, tool_status=None):
        hits.append(tool_status)
        return ConvergenceDecision(is_convergent=False, reason="insuffisant")

    ex._evaluate_semantic_convergence = fake_sem
    step = SimpleNamespace(type=StepType.ABSTRACT_TASK, expected_result="", id="s1")
    dec = asyncio.run(ex._check_convergence_inner(step, "result", action_success=True))
    assert hits == ["OK"]
    assert dec.is_convergent is False


def test_tool_call_empty_expected_still_implicit():
    from core.executor import Executor
    ex = Executor.__new__(Executor)

    async def boom(*a, **k):
        raise AssertionError("semantic ne doit pas être appelé pour un tool_call vide")

    ex._evaluate_semantic_convergence = boom
    step = SimpleNamespace(type=StepType.TOOL_CALL, expected_result="", id="s1")
    dec = asyncio.run(ex._check_convergence_inner(step, "result", action_success=True))
    assert dec.is_convergent is True


def test_convergence_prompt_has_empty_expected_rule():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en", "fr"):
        out = get_prompt_loader().load(
            "convergence.md", lang=lang, step_description="d",
            expected_result="", actual_result="r", tool_status="UNKNOWN")
        assert "is_convergent=false" in out

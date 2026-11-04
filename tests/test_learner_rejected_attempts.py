"""F5 : le learner doit produire des leçons, y compris sur les rejets.

Constat mémoire réelle (2026-10-31) : 4 épisodes, **0 leçon**, 0 skill.
Deux causes :

1. `_traverse_tree` ne retenait que `outcome == "failed"` : les rejets
   pré-exécution (`outcome == "rejected"`), qui sont pourtant des échecs de
   plan, n'étaient jamais analysés.
2. `_generate_avoid_lesson` faisait `if not entity_type: return`. Or un
   `convergence_failure` (mission Notepad) a `target_entity = None` : aucune
   leçon possible, alors que la doctrine dit « blame shifting → Planner ».
"""

import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.execution_models import ExecutionTree, FailureClass, PlanAttempt
from core.learner import Analyzer, ExtractedLesson


def _attempt(outcome, failure_class, target_entity=None, reason="panne"):
    return PlanAttempt(
        attempt_number=1,
        outcome=outcome,
        failure_class=failure_class,
        failure_reason=reason,
        target_entity=target_entity,
    )


def _tree(*attempts):
    return ExecutionTree(solver_id="s1", goal="g", attempts=list(attempts))


class _Ctx:
    def scope(self, **_kwargs):
        return nullcontext()


class _Store:
    embedding_model = None

    def __init__(self):
        self.calls = []

    def upsert_lesson(self, **kwargs):
        self.calls.append(kwargs)


class _Loader:
    def load(self, *_args, **_kwargs):
        return "prompt"


class _Llm:
    async def generate_structured(self, **_kwargs):
        return ExtractedLesson(
            scope="scope_x",
            keywords=["k"],
            recommendation="une piste consiste à vérifier le focus",
            polarity="avoid",
        )


def _analyzer():
    analyzer = Analyzer.__new__(Analyzer)
    analyzer.lesson_store = _Store()
    analyzer.llm = _Llm()
    analyzer.loader = _Loader()
    analyzer.runtime_state = SimpleNamespace(language="en", execution_context=_Ctx())
    return analyzer


async def test_rejected_attempt_is_analyzed():
    analyzer = _analyzer()
    seen = []

    async def _capture(attempt, *_args, **_kwargs):
        seen.append(attempt)

    analyzer._analyze_attempt = _capture

    await analyzer._traverse_tree(
        _tree(_attempt("rejected", FailureClass.PLAN_REJECTED_VALIDATION, "Planner")),
        "real",
        "m1",
    )

    assert len(seen) == 1
    assert seen[0].outcome == "rejected"


async def test_user_cancelled_is_ignored():
    analyzer = _analyzer()
    seen = []

    async def _capture(attempt, *_args, **_kwargs):
        seen.append(attempt)

    analyzer._analyze_attempt = _capture

    await analyzer._traverse_tree(
        _tree(_attempt("failed", FailureClass.USER_CANCELLED, "Executor")),
        "real",
        "m1",
    )

    assert seen == []


async def test_missing_target_entity_defaults_to_planner(monkeypatch):
    import core.learner as learner_module

    async def _embed(*_args, **_kwargs):
        return [0.0]

    monkeypatch.setattr(learner_module, "embed_managed", _embed)
    monkeypatch.setattr(learner_module, "get_entity_role", lambda name: "role")

    analyzer = _analyzer()
    attempt = _attempt("failed", FailureClass.CONVERGENCE_FAILURE, target_entity=None)

    await analyzer._generate_avoid_lesson(attempt, "g", "real", "m1")

    assert len(analyzer.lesson_store.calls) == 1
    assert analyzer.lesson_store.calls[0]["entity_type"] == "Planner"
    assert analyzer.lesson_store.calls[0]["polarity"] == "avoid"


async def test_rejected_attempt_produces_a_lesson(monkeypatch):
    import core.learner as learner_module

    async def _embed(*_args, **_kwargs):
        return [0.0]

    monkeypatch.setattr(learner_module, "embed_managed", _embed)
    monkeypatch.setattr(learner_module, "get_entity_role", lambda name: "role")

    analyzer = _analyzer()
    attempt = _attempt("rejected", FailureClass.PLAN_REJECTED_VALIDATION, "Planner")

    await analyzer._analyze_attempt(attempt, "g", "real", "m1")

    assert len(analyzer.lesson_store.calls) == 1

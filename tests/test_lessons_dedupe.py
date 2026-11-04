"""Tests dédup faits/leçons (le vrai doublon était à la lecture)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learner import dedupe_recommendations


def test_dedupe_keeps_first_drops_repeats():
    lessons = [
        {"recommendation": "User aime cmd", "polarity": "prefer"},
        {"recommendation": "user  aime   CMD ", "polarity": "prefer"},
        {"recommendation": "Éviter les rushs", "polarity": "avoid"},
    ]
    out = dedupe_recommendations(lessons)
    assert [l["recommendation"] for l in out] == ["User aime cmd", "Éviter les rushs"]


def test_dedupe_ignores_empties():
    lessons = [{"recommendation": ""}, {"recommendation": "  "}, {"recommendation": "ok"}]
    assert dedupe_recommendations(lessons) == [{"recommendation": "ok"}]


def test_store_reinforces_instead_of_duplicating(tmp_path):
    from memory.lesson_store import LessonStore
    store = LessonStore(str(tmp_path / "l.db"))
    store.upsert_lesson(entity_type="Orchestrator", scope="semantic_fact",
                        recommendation="User aime cmd", environment="simulated")
    store.upsert_lesson(entity_type="Orchestrator", scope="semantic_fact",
                        recommendation="user aime  CMD", environment="simulated")
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "l.db"))
    rows = con.execute(
        "SELECT evidence_count FROM lessons WHERE scope='semantic_fact'").fetchall()
    assert len(rows) == 1 and rows[0][0] == 2


# --------------------------------------------------------------------------
# P4.1 — pas de double écriture de leçon (préfixe français + même embedding)
# --------------------------------------------------------------------------

class _CaptureStore:
    def __init__(self):
        self.calls = []

    def upsert_lesson(self, **kwargs):
        self.calls.append(kwargs)


class _Cache:
    async def invalidate(self, scopes):
        return None


class _Scope:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _analyzer(store, recommendation="Faire X."):
    import asyncio
    from types import SimpleNamespace
    from core.learner import Analyzer, ExtractedLesson

    an = Analyzer.__new__(Analyzer)
    an.lesson_store = store
    an.loader = SimpleNamespace(load=lambda *a, **k: "prompt")
    an.runtime_state = SimpleNamespace(
        language="fr",
        cache_manager=_Cache(),
        execution_context=SimpleNamespace(scope=lambda **k: _Scope(**k)),
    )

    async def fake_structured(**kwargs):
        return ExtractedLesson(
            scope="planner_scope", keywords=["k"], recommendation=recommendation,
            polarity="avoid",
        )

    an.llm = SimpleNamespace(generate_structured=fake_structured)
    return an, asyncio


def _failed_attempt():
    from core.execution_models import ExecutionNode, FailureClass, PlanAttempt
    return PlanAttempt(
        attempt_number=1,
        outcome="failed",
        failure_class=FailureClass.EXECUTION_FAILURE,
        failure_reason="outil cassé",
        target_entity="Planner",
        nodes=[ExecutionNode(
            step_id="step_1", description="d", step_type="tool_call",
            tool_name="mouse", status="failed",
        )],
    )


def test_no_prefixed_duplicate_lesson(monkeypatch):
    import core.learner as learner_mod

    async def fake_embed(*a, **k):
        return [0.1, 0.2]

    monkeypatch.setattr(learner_mod, "embed_managed", fake_embed)
    store = _CaptureStore()
    an, asyncio = _analyzer(store)
    asyncio.run(an._generate_avoid_lesson(
        _failed_attempt(), goal="g", environment="simulated", mission_id="m1"))

    assert len(store.calls) == 1
    rec = store.calls[0]["recommendation"]
    assert not rec.lower().startswith("échec de l'outil")
    assert rec == "Faire X."


def test_same_embedding_not_written_twice(monkeypatch):
    import core.learner as learner_mod

    async def fake_embed(*a, **k):
        return [0.1, 0.2]

    monkeypatch.setattr(learner_mod, "embed_managed", fake_embed)
    store = _CaptureStore()
    an, asyncio = _analyzer(store)
    asyncio.run(an._generate_avoid_lesson(
        _failed_attempt(), goal="g", environment="simulated", mission_id="m1"))

    embeddings = [tuple(c.get("embedding") or ()) for c in store.calls]
    assert len(embeddings) == len(set(embeddings)) == 1

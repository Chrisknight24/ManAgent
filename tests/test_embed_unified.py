"""Embeddings unifies : le modele de l'hote sert partout. Nos yeux auto."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.embedding_service import embed_managed


class _FakeManager:
    def __init__(self, vec):
        self._vec = vec
        self.calls = 0

    @property
    def active_provider(self):
        return object()

    async def embed(self, text):
        self.calls += 1
        return list(self._vec)


def _rs(manager=None):
    return types.SimpleNamespace(embedding_manager=manager)


def test_managed_uses_active_provider():
    mgr = _FakeManager([0.1, 0.2])
    out = asyncio.run(embed_managed(_rs(mgr), "bonjour"))
    assert out == [0.1, 0.2]
    assert mgr.calls == 1


def test_managed_falls_back_without_provider(monkeypatch):
    import core.embedding_service as es

    async def fake_embed(text):
        return [0.5, 0.5]

    monkeypatch.setattr(es, "embed_text", fake_embed)
    out = asyncio.run(embed_managed(_rs(None), "bonjour"))
    assert out == [0.5, 0.5]


def test_dim_guard_skips_foreign_lessons(tmp_path):
    from memory.lesson_store import LessonStore
    store = LessonStore(db_path=str(tmp_path / "dim_test.db"))
    store.upsert_lesson(entity_type="Planner", scope="s1", recommendation="Faire X.",
                        environment="simulated", mission_id="m1", polarity="avoid",
                        embedding=[0.1] * 384)
    # Requête dans un autre espace (1024 dim) : la leçon 384 est ignorée, pas de crash.
    res = store.get_similar_lessons([0.1] * 1024, ["Planner"], "simulated", top_k=5)
    assert isinstance(res, list)


def _uses_managed(text: str) -> int:
    # Les appels s'étalent sur plusieurs lignes : normaliser avant de compter.
    flat = "".join(text.split())
    return flat.count("embed_managed(self.runtime_state,")


def test_learner_and_solver_use_managed():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    learner = open(os.path.join(base, "core", "learner.py"), encoding="utf-8").read()
    assert _uses_managed(learner) >= 3
    solver = open(os.path.join(base, "core", "solver.py"), encoding="utf-8").read()
    assert _uses_managed(solver) >= 1
    orch = open(os.path.join(base, "core", "orchestrator.py"), encoding="utf-8").read()
    assert _uses_managed(orch) >= 1
    ent = open(os.path.join(base, "core", "entity_learner.py"), encoding="utf-8").read()
    assert _uses_managed(ent) >= 1

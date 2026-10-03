"""Faits simples : montrer, marquer, jamais effacer. Nos yeux auto."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader


def test_known_facts_shown_with_instruction():
    out = get_prompt_loader().load(
        "orchestrator.md", lang="base", user_message="j'utilise Neovim",
        history="", advice="", model_id="m", supported_modalities=[],
        unsupported_modalities=[],
        known_facts="- L'utilisateur préfère Neovim (2026-09-12)")
    assert "Neovim" in out
    assert "CE message" in out
    assert "{{" not in out


def test_no_known_facts_no_noise():
    out = get_prompt_loader().load(
        "orchestrator.md", lang="base", user_message="bonjour",
        history="", advice="", model_id="m", supported_modalities=[],
        unsupported_modalities=[], known_facts="")
    assert "Déjà connus" not in out


class _FakeManager:
    @property
    def active_provider(self):
        return object()

    async def embed(self, text):
        return [0.2] * 384


def _learner(tmp_path):
    from core.entity_learner import EntityLearner
    from memory.lesson_store import LessonStore
    store = LessonStore(db_path=str(tmp_path / "faits_test.db"))
    rs = types.SimpleNamespace(embedding_manager=_FakeManager())
    return EntityLearner(lesson_store=store, cache_manager=None, runtime_state=rs), store


def _seed(store, n=3):
    texts = ["L'utilisateur aime le bleu.",
             "Le bleu est sa couleur de prédilection.",
             "Préférence enregistrée : interface bleue."]
    for i in range(n):
        store.upsert_lesson(entity_type="Orchestrator", scope="semantic_fact",
                            recommendation=texts[i % len(texts)],
                            environment="simulated", mission_id=f"m{i}",
                            polarity="prefer", embedding=[0.2] * 384)


def test_marking_stops_regrouping_without_deleting(tmp_path):
    learner, store = _learner(tmp_path)
    _seed(store, 3)
    assert len(store.get_unconsolidated_groups()) == 1
    done = asyncio.run(learner.consolidate_if_needed())
    assert done == 1
    assert store.get_unconsolidated_groups() == []
    # Rien effacé : 3 brutes toujours là et actives + 1 consolidée.
    all_rows = store.get_all_lessons()
    assert len(all_rows) == 4
    assert all(r.get("is_active", 1) == 1 for r in all_rows)


def test_similar_facts_ranked():
    import memory.lesson_store as _ls
    assert hasattr(_ls.LessonStore, "get_similar_facts")
    assert hasattr(_ls.LessonStore, "mark_group_consolidated")

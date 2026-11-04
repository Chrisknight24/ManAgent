"""Leçon Presentator : ne pas imputer l'échec de la MISSION au rapport.

Bug réel (mission `b39a11ae`, 28/10 21:47) : la mission Paint échoue, le
Presentator rédige un rapport correct, mais `presentator_result` porte
`status="failed"` — le statut de la MISSION. Le Learner en déduit
« final_report_generation_failure » et stocke une leçon contre le
Presentator. Le vrai coupable (plan/targeting) n'est pas celui-là.

Un vrai échec de génération est stocké par l'orchestrateur sous la forme
`{"status": "failed", "error_reason": ...}` SANS `user_response`.
"""

import asyncio
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learner import Analyzer, ExtractedLesson, is_presentator_generation_failure


# ---------------------------------------------------------------------------
# Table de vérité (fonction pure)
# ---------------------------------------------------------------------------

def test_mission_failure_with_a_report_is_not_a_presentator_failure():
    assert not is_presentator_generation_failure({
        "status": "failed",
        "user_response": "❌ La mission a échoué : aucun carré tracé.",
        "system_summary": "Échec de la mission.",
        "error_reason": "convergence",
    })


def test_genuine_generation_crash_is_detected():
    assert is_presentator_generation_failure({"status": "failed", "error_reason": "boom"})
    assert is_presentator_generation_failure({"status": "failed", "generation_failed": True})


def test_partial_success_and_empty_are_not_generation_failures():
    assert not is_presentator_generation_failure({"status": "partial", "user_response": "..."})
    assert not is_presentator_generation_failure({"status": "success", "user_response": "..."})
    assert not is_presentator_generation_failure(None)
    assert not is_presentator_generation_failure({})


# ---------------------------------------------------------------------------
# Comportement de l'Analyzer
# ---------------------------------------------------------------------------

class _FakeStore:
    embedding_model = None

    def __init__(self):
        self.calls = []

    def upsert_lesson(self, **kwargs):
        self.calls.append(kwargs)


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def generate_structured(self, **kwargs):
        self.calls += 1
        return ExtractedLesson(
            scope="final_report_generation_failure",
            keywords=["presentator"],
            recommendation="Vérifier le contexte.",
            polarity="avoid",
        )


class _FakeCtx:
    def scope(self, **kwargs):
        return nullcontext()


def _analyzer():
    store = _FakeStore()
    llm = _FakeLLM()
    runtime_state = SimpleNamespace(
        language="en",
        execution_context=_FakeCtx(),
        cache_manager=None,
    )
    analyzer = Analyzer(store, llm, runtime_state)
    analyzer._invalidate_advisor_cache = AsyncMock()
    return analyzer, store, llm


def _episode(result_json):
    return {
        "presentator_result_json": result_json,
        "mission_id": "m1",
        "environment": "real",
    }


def test_normal_failed_mission_does_not_create_a_lesson(monkeypatch):
    async def fake_embed(*args, **kwargs):
        return None

    monkeypatch.setattr("core.learner.embed_managed", fake_embed)

    analyzer, store, llm = _analyzer()
    episode = _episode(
        '{"status": "failed", "user_response": "rapport correct", '
        '"system_summary": "échec mission", "error_reason": "convergence"}'
    )

    asyncio.run(analyzer._analyze_presentator_failure(episode))

    assert llm.calls == 0
    assert store.calls == []


def test_genuine_generation_crash_still_creates_a_lesson(monkeypatch):
    async def fake_embed(*args, **kwargs):
        return None

    monkeypatch.setattr("core.learner.embed_managed", fake_embed)

    analyzer, store, llm = _analyzer()
    episode = _episode('{"status": "failed", "error_reason": "service indisponible"}')

    asyncio.run(analyzer._analyze_presentator_failure(episode))

    assert llm.calls == 1
    assert len(store.calls) == 1
    assert store.calls[0]["entity_type"] == "Presentator"

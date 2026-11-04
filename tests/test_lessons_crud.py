import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.entity_learner import EntityLearner
from memory.lesson_store import LessonStore


@pytest.fixture
def store(tmp_path):
    return LessonStore(db_path=str(tmp_path / "lessons_crud.db"))


def test_upsert_sets_public_identity(store):
    store.upsert_lesson(entity_type="Planner", scope="s1", recommendation="A")
    lesson = store.list_lessons()[0]
    assert lesson["uid"]
    assert lesson["origin"] == "generated"
    assert lesson["revision"] == 1
    assert lesson["updated_at"]


def test_create_get_update_soft_delete(store):
    created = store.create_lesson(
        entity_type="Planner", scope="manual", recommendation="Faire X",
        polarity="prefer", origin="manual", locked=True,
    )
    assert created["origin"] == "manual" and created["locked"] is True

    with pytest.raises(PermissionError):
        store.update_lesson(created["uid"], {"recommendation": "Y"})

    updated = store.update_lesson(
        created["uid"], {"recommendation": "Faire Y", "keywords": ["y"]},
        force=True,
    )
    assert updated["recommendation"] == "Faire Y"
    assert updated["revision"] == 2
    assert updated["keywords"] == ["y"]

    assert store.delete_lesson(created["uid"], force=True) is True
    assert store.get_lesson(created["uid"])["is_active"] is False
    assert store.list_lessons() == []
    assert store.list_lessons(include_inactive=True)[0]["uid"] == created["uid"]


def test_stale_revision_is_refused(store):
    created = store.create_lesson(
        entity_type="Planner", scope="s", recommendation="A",
    )
    with pytest.raises(ValueError):
        store.update_lesson(created["uid"], {"confidence": 0.9}, expected_revision=99)


def test_hard_delete_removes_row(store):
    created = store.create_lesson(
        entity_type="Planner", scope="s", recommendation="A",
    )
    assert store.delete_lesson(created["uid"], hard=True) is True
    assert store.get_lesson(created["uid"]) is None


def test_orchestrator_lesson_actions(store):
    from core.constants import Actions
    from core.orchestrator import Orchestrator
    from transport.packet_models import RequestPacket

    class Dummy:
        lesson_store = store
        runtime_state = types.SimpleNamespace(learner=None)

    dummy = Dummy()

    async def call(action, payload):
        return await Orchestrator._handle_lesson_action(
            dummy, RequestPacket(action=action, payload=payload)
        )

    created = asyncio.run(call(Actions.LESSON_CREATE, {
        "entity_type": "Planner", "scope": "api", "recommendation": "A",
    }))
    uid = created.payload["lesson"]["uid"]

    fetched = asyncio.run(call(Actions.LESSON_GET, {"uid": uid}))
    assert fetched.payload["lesson"]["uid"] == uid

    updated = asyncio.run(call(Actions.LESSON_UPDATE, {
        "uid": uid, "recommendation": "B", "expected_revision": 1,
    }))
    assert updated.payload["lesson"]["revision"] == 2

    listed = asyncio.run(call(Actions.LESSON_LIST, {"limit": 10}))
    assert listed.payload["count"] == 1

    deleted = asyncio.run(call(Actions.LESSON_DELETE, {"uid": uid}))
    assert deleted.payload["deleted"] is True


def test_consolidation_uses_cumulated_evidence(store, monkeypatch):
    for _ in range(3):
        store.upsert_lesson(
            entity_type="Planner", scope="same", recommendation="Éviter X",
            polarity="avoid",
        )
    groups = store.get_unconsolidated_groups()
    assert len(groups) == 1
    assert groups[0]["raw_count"] == 1

    async def _no_embedding(*args, **kwargs):
        return None

    fake = types.ModuleType("core.embedding_service")
    fake.embed_managed = _no_embedding
    monkeypatch.setitem(sys.modules, "core.embedding_service", fake)
    learner = EntityLearner(store)
    assert asyncio.run(learner.consolidate_if_needed()) == 1

    lessons = store.list_lessons()
    consolidated = [l for l in lessons if l.get("consolidated_from_id")]
    assert len(consolidated) == 1
    assert consolidated[0]["origin"] == "generated"
    assert consolidated[0]["uid"]
    assert consolidated[0]["evidence_count"] == 3

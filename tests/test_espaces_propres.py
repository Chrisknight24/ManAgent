"""Espaces propres : aucun vecteur hors de son espace. Tests rassurants."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BGE = "BAAI/bge-m3"
MINI = "sentence-transformers/all-MiniLM-L6-v2"


def _lesson_store(tmp_path, model=None):
    from memory.lesson_store import LessonStore
    return LessonStore(db_path=str(tmp_path / "espaces_test.db"), embedding_model=model)


def test_switch_model_no_cross_hits(tmp_path):
    store = _lesson_store(tmp_path, MINI)
    store.upsert_lesson(entity_type="Planner", scope="s1", recommendation="Faire X.",
                        environment="simulated", mission_id="m1", polarity="avoid",
                        embedding=[0.5] * 384)
    store.use_embedding_model(BGE)
    store.upsert_lesson(entity_type="Planner", scope="s1", recommendation="Faire Y.",
                        environment="simulated", mission_id="m2", polarity="avoid",
                        embedding=[0.5] * 1024)
    # Requête espace BGE : seule la ligne BGE revient.
    res = store.get_similar_lessons([0.5] * 1024, ["Planner"], "simulated", top_k=5)
    recs = [r["recommendation"] for r in res]
    assert "Faire Y." in recs
    assert "Faire X." not in recs


def test_legacy_rows_backfilled_historic(tmp_path):
    import sqlite3
    from memory.lesson_store import LessonStore
    path = str(tmp_path / "legacy_test.db")
    store = LessonStore(db_path=path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO lessons (entity_type, scope, recommendation, environment, "
        "confidence, evidence_count, polarity, is_consolidated, is_active) "
        "VALUES ('Planner', 'vieux', 'Fait ancien.', 'simulated', 0.67, 1, 'prefer', 0, 1)")
    con.commit()
    con.close()
    store2 = LessonStore(db_path=path)  # migrations rejouées
    con = sqlite3.connect(path)
    row = con.execute("SELECT embedding_model FROM lessons WHERE scope = 'vieux'").fetchone()
    con.close()
    assert row[0] is not None and "MiniLM" in row[0]


def test_fallback_wrong_space_returns_none(monkeypatch):
    import core.embedding_service as es

    async def fake_embed(text):
        return [0.1] * 384

    monkeypatch.setattr(es, "embed_text", fake_embed)
    rs = types.SimpleNamespace(embedding_manager=None)
    out = asyncio.run(es.embed_managed(rs, "bonjour", expect_model=BGE))
    assert out is None  # pas de vecteur menteur


def test_fallback_matching_space_kept(monkeypatch):
    import core.embedding_service as es

    async def fake_embed(text):
        return [0.1] * 384

    monkeypatch.setattr(es, "embed_text", fake_embed)
    rs = types.SimpleNamespace(embedding_manager=None)
    out = asyncio.run(es.embed_managed(rs, "bonjour", expect_model=MINI))
    assert out == [0.1] * 384


def test_profile_store_synced_and_tolerant(tmp_path):
    from memory.mission_profile_store import MissionProfileStore
    store = MissionProfileStore(db_path=str(tmp_path / "prof_test.db"))
    store.use_embedding_model(BGE)
    assert store._vec_table != "vec_mission_profiles"  # namespace dédié, pas le défaut
    pid = store.insert_profile(
        mission_id="m1", signature_text="ouvrir notepad", embedding=None,
        action="ouvrir", object="notepad", embedding_model=BGE,
        embedding_dimension=1024)
    assert pid > 0  # comptage par hash, sans vecteur
    cid, n = store.record_execution_result_vectorial(
        signature_text="ouvrir notepad", embedding=None, is_success=True,
        action="ouvrir", object_name="notepad")
    assert cid > 0 and n >= 1

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

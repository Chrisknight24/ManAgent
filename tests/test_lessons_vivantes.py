"""Leçons vivantes : la confiance bouge selon les preuves. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.lesson_store import LessonStore


def _store(tmp_path):
    return LessonStore(db_path=str(tmp_path / "lessons_test.db"))


def test_identical_lesson_reinforces_instead_of_duplicating(tmp_path):
    s = _store(tmp_path)
    s.upsert_lesson(entity_type="Planner", scope="s1", recommendation="Faire X.",
                    environment="simulated", mission_id="m1", polarity="avoid")
    s.upsert_lesson(entity_type="Planner", scope="s1", recommendation="Faire X.",
                    environment="simulated", mission_id="m2", polarity="avoid")
    lessons = s.get_all_lessons()
    assert len(lessons) == 1
    assert lessons[0]["evidence_count"] == 2
    assert lessons[0]["confidence"] > 0.67
    assert "m1" in lessons[0]["source_episodes"] and "m2" in lessons[0]["source_episodes"]


def test_opposite_polarity_contradicts_without_deleting(tmp_path):
    s = _store(tmp_path)
    s.upsert_lesson(entity_type="Planner", scope="s2", recommendation="Ne jamais faire Y.",
                    environment="simulated", mission_id="m1", polarity="avoid")
    s.upsert_lesson(entity_type="Planner", scope="s2", recommendation="Faire Y aide.",
                    environment="simulated", mission_id="m2", polarity="prefer")
    lessons = s.get_all_lessons()
    assert len(lessons) == 2
    avoid = next(l for l in lessons if l["polarity"] == "avoid")
    assert avoid["contradiction_count"] >= 1
    assert avoid["confidence"] < 0.67


def test_different_text_same_scope_keeps_distinct_rows(tmp_path):
    s = _store(tmp_path)
    s.upsert_lesson(entity_type="Planner", scope="s3", recommendation="Faire A.",
                    environment="simulated", mission_id="m1", polarity="avoid")
    s.upsert_lesson(entity_type="Planner", scope="s3", recommendation="Faire B.",
                    environment="simulated", mission_id="m2", polarity="avoid")
    assert len(s.get_all_lessons()) == 2


def test_prompts_frame_lessons_as_fallible_reports():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "en", "fr"):
        for name in ("planner.md", "feasibility.md", "orchestrator.md",
                     "presentator_report.md", "presentator_error.md"):
            text = open(os.path.join(base, "prompts", lang, name), encoding="utf-8").read()
            assert "JUGÉES" in text or "JUDGED" in text, f"{lang}/{name}"
            assert "jamais des ordres" in text or "never orders" in text, f"{lang}/{name}"

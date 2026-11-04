import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.lesson_store import LessonStore
from tools.build_observability_report import render_html


def _store(tmp_path):
    return LessonStore(db_path=str(tmp_path / "lessons_fixes.db"))


def test_lesson_count_is_returned(tmp_path):
    store = _store(tmp_path)
    store.upsert_lesson(entity_type="Planner", scope="s", recommendation="A",
                        environment="simulated")
    store.upsert_lesson(entity_type="Planner", scope="s2", recommendation="B",
                        environment="simulated")
    assert store.get_lessons_count() == 2


def test_similar_lessons_are_filtered_by_environment(tmp_path):
    store = _store(tmp_path)
    store.upsert_lesson(entity_type="Planner", scope="same", recommendation="sim only",
                        environment="simulated")
    store.upsert_lesson(entity_type="Planner", scope="same", recommendation="real only",
                        environment="real")
    result = store.get_similar_lessons([0.0] * 384, ["Planner"], "real", top_k=10)
    texts = [row["recommendation"] for row in result]
    assert "real only" in texts
    assert "sim only" not in texts


def test_copy_uses_registry_not_inline_json():
    html = render_html({"episodes": [], "lessons": [], "skills": [], "sessions": []})
    assert "copyBubbleText" in html
    assert "JSON.stringify(uMsg)" not in html


def test_script_end_tag_is_escaped_case_insensitively():
    html = render_html({"episodes": [{"x": "</SCRIPT>"}], "lessons": [],
                        "skills": [], "sessions": []})
    assert "</SCRIPT>" not in html


def test_target_lifecycle_view_is_wired():
    from tools.build_observability_report import TARGET_EVENTS
    html = render_html({"episodes": [], "lessons": [], "skills": [], "sessions": []})
    assert "renderCentralTargets" in html
    assert "Cibles — cycle de vie" in html
    assert "target.purged" in TARGET_EVENTS

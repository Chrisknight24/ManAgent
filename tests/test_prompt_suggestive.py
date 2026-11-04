import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def test_lesson_prompts_are_suggestive_in_all_languages():
    for lang in ("base", "fr", "en"):
        for name in ("generate_avoid_lesson.md", "generate_prefer_lesson.md",
                     "analyze_presentator_failure.md"):
            text = _read(f"prompts/{lang}/{name}")
            assert "règle impérative" not in text, f"{lang}/{name}"
            assert "imperative rule" not in text, f"{lang}/{name}"
    assert "one lead is to" in _read("prompts/en/generate_avoid_lesson.md")
    assert "often preferable" in _read("prompts/en/generate_prefer_lesson.md")


def test_planner_does_not_force_a_cosmetic_change():
    for lang in ("base", "fr"):
        text = _read(f"prompts/{lang}/planner.md")
        assert "pas seulement l'attente" not in text
        assert "change d'approche" not in text
        assert "évite de changer uniquement pour paraître différent" in text
    en = _read("prompts/en/planner.md")
    assert "never replay" not in en
    assert "avoid changing only to look different" in en


def test_plan_validation_presents_novelty_as_a_clue():
    for lang in ("base", "fr"):
        text = _read(f"prompts/{lang}/plan_validation.md")
        assert "fait, pas une opinion" not in text
        assert "indice, pas une preuve" in text
    en = _read("prompts/en/plan_validation.md")
    assert "fact, not an opinion" not in en
    assert "clue, not proof" in en

"""A2+A3b : le juge de convergence doit (1) exiger l'identité littérale de
l'élément visé et (2) rappeler qu'un statut outil `OK` ne prouve que le geste
technique, pas le monde visé."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _render(lang):
    from core.prompt_loader import get_prompt_loader
    return get_prompt_loader().load(
        "convergence.md", lang=lang, step_description="d",
        expected_result="e", actual_result="r", tool_status="OK")


def test_convergence_prompt_has_identity_rule():
    for lang in ("base", "en", "fr"):
        out = _render(lang)
        assert "identité" in out or "Identity" in out
        assert "SIMILAIRE" in out or "SIMILAR" in out


def test_convergence_prompt_tool_status_is_only_technical():
    for lang in ("base", "en", "fr"):
        out = _render(lang)
        assert "monde visé" in out or "intended target" in out

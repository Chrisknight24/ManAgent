"""Observabilité mission 1 : badge modèle + durées humaines présents."""
import os

BUILDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools", "build_observability_report.py",
)


def _src():
    with open(BUILDER, encoding="utf-8") as f:
        return f.read()


def test_model_badge_helper_exists():
    src = _src()
    assert "function modelBadge(call)" in src
    assert "provider_id" in src and "model_id" in src


def test_human_duration_shows_both():
    src = _src()
    assert "formatDuration(c.duration_ms)}) ${modelBadge(c)}" in src


def test_format_duration_dual_unit():
    src = _src()
    assert "sec.toFixed(1) + 's ('" in src

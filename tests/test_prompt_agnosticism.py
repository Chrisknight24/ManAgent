"""Agnosticité des prompts : aucun comportement/nom propre à un hôte.

Le prompt « cerveau » est partagé par plusieurs hôtes computer-use. Un
comportement d'hôte (raccourci de bascule, classe de fenêtre, nom d'outil)
relève de l'outil computer-use de l'hôte, jamais du prompt générique.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Motifs qui trahissent une fixette sur un hôte concret.
HOST_SPECIFIC_PATTERNS = (
    r"\btoggle\b",
    r"\bbascule\b",
    r"\bwin\s*\+\s*d\b",
    r"\bworkerw\b",
    r"\bprogram manager\b",
)


def _prompt_files():
    return sorted((ROOT / "prompts").rglob("*.md"))


def test_generic_prompts_never_encode_host_specific_behaviour():
    hits = []
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8").lower()
        for pattern in HOST_SPECIFIC_PATTERNS:
            for match in re.finditer(pattern, text):
                hits.append(f"{path.relative_to(ROOT)}: {match.group(0)!r}")
    assert not hits, "Prompt(s) non agnostique(s) : " + "; ".join(hits)


def test_planner_prompt_has_no_toggle_rule_in_any_locale():
    for lane in ("base", "fr", "en"):
        path = ROOT / "prompts" / lane / "planner.md"
        text = path.read_text(encoding="utf-8").lower()
        assert "toggle" not in text
        assert "bascule" not in text

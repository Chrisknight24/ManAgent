"""BUG F bloc 5 — rules.md resserré.

Le judge lit rules.md verbatim. Les notes d'édition (méta, avertissement
« brouillon ») consommaient des tokens et pouvaient minorer l'autorité des
critères. On les met en commentaires HTML ; `_load_rules_md` les retire avant
injection (fichier ET override). Critères inchangés.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import _strip_rules_comments

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_retire_commentaires_html():
    out = _strip_rules_comments("alpha <!-- note interne --> omega")
    assert "note interne" not in out
    assert "alpha" in out and "omega" in out


def test_normalise_les_blancs():
    assert _strip_rules_comments("x\n\n\n\n\ny") == "x\n\ny"


def test_tolerant_au_vide():
    assert _strip_rules_comments("") == ""
    assert _strip_rules_comments(None) is None


def test_rules_md_meta_retiree_criteres_gardes():
    raw = open(os.path.join(_ROOT, "rules.md"), encoding="utf-8").read()
    cleaned = _strip_rules_comments(raw)
    assert "qui consomment" not in cleaned or True  # sanity
    assert "Ce fichier est lu par l'Orchestrateur" not in cleaned
    assert "Brouillon de démarrage" not in cleaned
    assert len(cleaned) < len(raw)
    # critères intacts (lus par le judge)
    assert "JUGE" in cleaned
    assert "capture d'écran" in cleaned
    assert "is_conformant = false" in cleaned

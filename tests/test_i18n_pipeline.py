"""P4.2 — i18n réelle : extraction `_()` → .po → .mo chargeable.

Avant : `locale/en|fr/messages.po` faisaient 0 octet et aucun `.mo` n'existait.
`gettext.translation(..., fallback=True)` retombait donc sur NullTranslations :
`_()` était un no-op silencieux, et tout le français restait codé en dur.
"""
import gettext
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.i18n_compiler import (
    compile_mo,
    ensure_catalogs,
    extract_msgids,
    read_po,
    write_po,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_extracts_offender_strings():
    ids = extract_msgids(ROOT)
    assert "Les réponses directes sont toujours acceptées." in ids
    assert "Raison inconnue" in ids
    assert any("Si tu TROUVES ce qui est demandé" in i for i in ids)


def test_po_roundtrip_preserves_newlines(tmp_path):
    path = str(tmp_path / "messages.po")
    write_po(path, {"ligne 1\nligne 2": "line 1\nline 2"})
    assert read_po(path) == {"ligne 1\nligne 2": "line 1\nline 2"}


def test_mo_loadable_and_translates(tmp_path):
    po = tmp_path / "fr" / "LC_MESSAGES" / "messages.po"
    mo = tmp_path / "fr" / "LC_MESSAGES" / "messages.mo"
    write_po(str(po), {"Bonjour": "Hello", "Échec": "Failure"})
    compile_mo(read_po(str(po)), str(mo))

    tr = gettext.translation("messages", localedir=str(tmp_path), languages=["fr"])
    assert tr.gettext("Bonjour") == "Hello"
    assert tr.gettext("Échec") == "Failure"


def test_untranslated_entry_falls_back_to_msgid(tmp_path):
    # Un msgstr vide ne doit PAS être compilé : sinon gettext renvoie "".
    po = tmp_path / "fr" / "LC_MESSAGES" / "messages.po"
    mo = tmp_path / "fr" / "LC_MESSAGES" / "messages.mo"
    write_po(str(po), {"Bonjour": "Hello", "Pas encore traduit": ""})
    compile_mo(read_po(str(po)), str(mo))

    tr = gettext.translation("messages", localedir=str(tmp_path), languages=["fr"])
    assert tr.gettext("Bonjour") == "Hello"
    assert tr.gettext("Pas encore traduit") == "Pas encore traduit"


def test_catalog_covers_all_extracted(tmp_path):
    locale_dir = str(tmp_path / "locale")
    actions = ensure_catalogs(locale_dir, ROOT)
    assert "en" in actions and "fr" in actions

    ids = extract_msgids(ROOT)
    for lang in ("en", "fr"):
        catalog = read_po(os.path.join(locale_dir, lang, "LC_MESSAGES", "messages.po"))
        assert ids <= set(catalog), lang
        assert os.path.exists(
            os.path.join(locale_dir, lang, "LC_MESSAGES", "messages.mo"))


def test_ensure_catalogs_idempotent(tmp_path):
    locale_dir = str(tmp_path / "locale")
    ensure_catalogs(locale_dir, ROOT)
    mo = os.path.join(locale_dir, "en", "LC_MESSAGES", "messages.mo")
    before = os.path.getmtime(mo)
    assert ensure_catalogs(locale_dir, ROOT) == []
    assert os.path.getmtime(mo) == before

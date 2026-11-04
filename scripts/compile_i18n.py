#!/usr/bin/env python3
"""Compile les catalogues i18n (`_()` → .po → .mo`) sans dépendance externe.

Usage :
    python scripts/compile_i18n.py            # reconstruit si nécessaire
    python scripts/compile_i18n.py --check    # échoue si un catalogue est vide
    python scripts/compile_i18n.py --force    # reconstruit toujours
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.i18n_compiler import (  # noqa: E402
    compile_mo,
    ensure_catalogs,
    extract_msgids,
    read_po,
    write_po,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compile les catalogues i18n.")
    parser.add_argument("--check", action="store_true",
                        help="échoue si un .mo manque ou si un msgid n'est pas catalogué")
    parser.add_argument("--force", action="store_true",
                        help="reconstruit les .po/.mo même s'ils semblent à jour")
    parser.add_argument("--locale-dir", default=os.path.join(ROOT, "locale"))
    args = parser.parse_args(argv)

    if args.check:
        msgids = extract_msgids(ROOT)
        problems = []
        for lang in ("en", "fr"):
            po = os.path.join(args.locale_dir, lang, "LC_MESSAGES", "messages.po")
            mo = os.path.join(args.locale_dir, lang, "LC_MESSAGES", "messages.mo")
            catalog = read_po(po)
            missing = sorted(m for m in msgids if m not in catalog)
            if not os.path.exists(mo):
                problems.append(f"{lang}: .mo absent")
            if missing:
                problems.append(f"{lang}: {len(missing)} msgid(s) non catalogué(s)")
        if problems:
            print("\n".join(problems))
            return 1
        print(f"OK — {len(msgids)} chaînes cataloguées (en, fr)")
        return 0

    if args.force:
        msgids = extract_msgids(ROOT)
        for lang in ("en", "fr"):
            lang_dir = os.path.join(args.locale_dir, lang, "LC_MESSAGES")
            po = os.path.join(lang_dir, "messages.po")
            catalog = read_po(po)
            for msgid in msgids:
                catalog.setdefault(msgid, "")
            write_po(po, catalog)
            compile_mo(catalog, os.path.join(lang_dir, "messages.mo"))
            print(f"{lang}: {len(catalog)} entrées compilées")
        return 0

    actions = ensure_catalogs(args.locale_dir, ROOT)
    print("Reconstruits : " + (", ".join(actions) if actions else "aucun (à jour)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

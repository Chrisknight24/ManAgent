"""
core/i18n_compiler.py
=====================
Extraction des chaînes `_(...)` et compilation `.po` → `.mo`, sans dépendance
externe (ni `xgettext`, ni `msgfmt`).

Pourquoi : `locale/en|fr/messages.po` étaient vides et aucun `.mo` n'existait,
donc `_()` était un no-op silencieux. Ce module rend la chaîne réelle :
extraction AST → catalogue `.po` → `.mo` chargeable par gettext.

Agnostique : aucune chaîne métier n'est codée ici, seulement l'outillage.
"""
from __future__ import annotations

import ast
import os
import struct
from typing import Dict, Iterable, List, Set

SKIP_DIRS = {
    ".git", ".venv", "__pycache__", "locale", "tests", "docs",
    "node_modules", ".pytest_cache", "build", "dist",
}


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_msgids(source_root: str) -> Set[str]:
    """Toutes les chaînes littérales passées à `_(...)` sous `source_root`."""
    found: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "_":
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return found


# --------------------------------------------------------------------------
# .po (lecture / écriture)
# --------------------------------------------------------------------------

def _po_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def _po_unescape(text: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
            out.append(mapping.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def read_po(path: str) -> Dict[str, str]:
    """Catalogue simple : msgid → msgstr (ignore l'en-tête vide)."""
    entries: Dict[str, str] = {}
    if not os.path.exists(path):
        return entries
    msgid = None
    msgstr = None
    current = None
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("msgid "):
                    if msgid is not None and msgid != "":
                        entries[msgid] = msgstr or ""
                    msgid = _po_unescape(line[6:].strip().strip('"'))
                    msgstr = ""
                    current = "id"
                elif line.startswith("msgstr "):
                    msgstr = _po_unescape(line[7:].strip().strip('"'))
                    current = "str"
                elif line.startswith('"') and current:
                    part = _po_unescape(line.strip('"'))
                    if current == "id":
                        msgid = (msgid or "") + part
                    else:
                        msgstr = (msgstr or "") + part
        if msgid is not None and msgid != "":
            entries[msgid] = msgstr or ""
    except Exception:
        return {}
    return entries


def write_po(path: str, catalog: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for msgid in sorted(catalog):
        lines.append(f'msgid "{_po_escape(msgid)}"')
        lines.append(f'msgstr "{_po_escape(catalog[msgid])}"')
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


# --------------------------------------------------------------------------
# .mo (écriture binaire GNU, sans msgfmt)
# --------------------------------------------------------------------------

def compile_mo(catalog: Dict[str, str], out_path: str) -> None:
    """Compile un catalogue en `.mo` (format GNU gettext, little endian)."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    entries = {
        "": (
            "Content-Type: text/plain; charset=UTF-8\n"
            "Content-Transfer-Encoding: 8bit\n"
        )
    }
    entries.update({k: v for k, v in catalog.items() if k and v})
    ordered = sorted(entries.items(), key=lambda kv: kv[0].encode("utf-8"))

    count = len(ordered)
    header_size = 7 * 4
    table_size = count * 8
    orig_start = header_size + table_size * 2

    originals = b""
    orig_table = []
    offset = orig_start
    for msgid, _msgstr in ordered:
        o_bytes = msgid.encode("utf-8")
        orig_table.append((len(o_bytes), offset))
        originals += o_bytes + b"\x00"
        offset += len(o_bytes) + 1

    translated = b""
    trans_table = []
    trans_start = offset
    offset = trans_start
    for _msgid, msgstr in ordered:
        t_bytes = msgstr.encode("utf-8")
        trans_table.append((len(t_bytes), offset))
        translated += t_bytes + b"\x00"
        offset += len(t_bytes) + 1

    header = struct.pack(
        "<7I",
        0x950412DE, 0, count,
        header_size,
        header_size + table_size,
        0, 0,
    )
    table_orig = b"".join(struct.pack("<2I", length, off) for length, off in orig_table)
    table_trans = b"".join(struct.pack("<2I", length, off) for length, off in trans_table)
    with open(out_path, "wb") as handle:
        handle.write(header + table_orig + table_trans + originals + translated)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def _newest_py_mtime(source_root: str) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(dirpath, filename)))
                except OSError:
                    continue
    return newest


def ensure_catalogs(locale_dir: str, source_root: str, domain: str = "messages",
                    languages: Iterable[str] = ("en", "fr")) -> List[str]:
    """Extrait et compile si `.po` vide ou `.mo` absent/ancien.

    Retourne la liste des catalogues reconstruits (diagnostic). N'échoue
    jamais : une erreur d'i18n ne doit pas empêcher le démarrage.
    """
    actions: List[str] = []
    try:
        msgids = extract_msgids(source_root)
    except Exception:
        msgids = set()
    newest_source = 0.0
    try:
        newest_source = _newest_py_mtime(source_root)
    except Exception:
        pass

    for lang in languages:
        lang_dir = os.path.join(locale_dir, lang, "LC_MESSAGES")
        po_path = os.path.join(lang_dir, f"{domain}.po")
        mo_path = os.path.join(lang_dir, f"{domain}.mo")
        try:
            existing = read_po(po_path)
        except Exception:
            existing = {}
        stale_po = not existing
        stale_mo = not os.path.exists(mo_path)
        if not stale_mo:
            try:
                mo_mtime = os.path.getmtime(mo_path)
                po_mtime = os.path.getmtime(po_path) if os.path.exists(po_path) else 0
                stale_mo = mo_mtime < po_mtime or mo_mtime < newest_source
            except OSError:
                stale_mo = True
        if not (stale_po or stale_mo):
            continue
        catalog = dict(existing)
        for msgid in msgids:
            catalog.setdefault(msgid, "")
        try:
            write_po(po_path, catalog)
            compile_mo(catalog, mo_path)
            actions.append(lang)
        except Exception:
            continue
    return actions

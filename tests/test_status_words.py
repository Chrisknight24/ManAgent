"""Statuts courts : l'hote affiche un mot, pas une phrase. Nos yeux auto."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALLOWED = {"analyzing", "delegating", "solving", "retrieving", "planning",
           "validating", "reporting", "exploring"}

FILES = [
    "core/solver.py",
    "core/planner.py",
    "core/orchestrator.py",
    "core/discovery/discovery_session.py",
]


def test_status_updates_are_short_words():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = []
    for rel in FILES:
        text = open(os.path.join(base, rel), encoding="utf-8").read()
        for m in re.finditer(r'STATUS_UPDATE,\s*\{"message":\s*(?:_\()?([^})\n]+)', text):
            raw = m.group(1).strip()
            if raw == "msg":
                mm = re.search(r'^\s*msg\s*=\s*("[^"]*"|\'[^\']*\')', text, re.M)
                raw = mm.group(1) if mm else raw
            found.append((rel, raw))
    assert found, "aucun status.update trouve"
    for rel, raw in found:
        word = raw.strip("\"'f").strip("\"'")
        assert word in ALLOWED, f"{rel} : message trop long ou inconnu : {raw!r}"
    words = {w for _, r in found for w in [r.strip("\"'f").strip("\"'")]}
    assert words == ALLOWED, f"mots manquants : {ALLOWED - words}"

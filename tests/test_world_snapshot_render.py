"""P2.1 — rendu du world state : jamais de coupe en plein mot.

Bug réel (mission `4a810e6e`, 29/10) : le payload hôte faisait 997 chars pour
un budget Planner de 600, et la coupe tombait au milieu d'un mot (« Age… »).
Le modèle lisait un état tronqué et incompréhensible.
"""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import fetch_world_state_snapshot, render_world_snapshot


def _tokens(text):
    return set(text.replace("…", " ").split())


def test_no_mid_word_cut():
    original = "Age of Empires est ouvert au premier plan avec une fenetre de jeu"
    out = render_world_snapshot(original, limit=20)
    assert out.endswith("…")
    assert len(out) <= 20
    assert _tokens(out) <= _tokens(original), (out, original)


def test_priority_summary_first():
    data = {"windows": [{"title": "Paint"}], "summary_text": "Paint est ouvert"}
    out = render_world_snapshot(data, limit=400)
    assert out.index("summary_text") < out.index("windows")


def test_all_windows_listed_when_budget_allows():
    data = {"windows": [{"title": f"w{i}"} for i in range(6)], "focus": "Paint"}
    out = render_world_snapshot(data, limit=800)
    for i in range(6):
        assert f"w{i}" in out


def test_host_max_chars_honored():
    data = {"summary_text": "x " * 500, "windows": [{"title": "Paint"}]}
    out = render_world_snapshot(data, limit=120)
    assert len(out) <= 120


def test_omitted_fields_are_counted():
    data = {
        "summary_text": "ok",
        "windows": [{"title": "Paint"}],
        "targets": [{"name": "outil rectangle"}],
        "extra_a": "a" * 200,
        "extra_b": "b" * 200,
    }
    out = render_world_snapshot(data, limit=80)
    assert "omis" in out


def test_list_cap_still_counts_items():
    data = {"windows": [{"title": f"w{i}"} for i in range(25)]}
    out = render_world_snapshot(data, limit=10000)
    assert "w9" in out and "w10" not in out
    assert "+15 entrées 'windows' non listées" in out


def test_fetch_honors_host_max_chars():
    class FakeTM:
        def known_tool_names(self):
            return {"get_world_state"}

        async def execute_tool(self, name, args):
            return json.dumps({
                "result": True,
                "data": {"summary_text": "Age of Empires " * 100},
            })

    rs = types.SimpleNamespace(tools_manager=FakeTM(), host_manifest=None)
    out = asyncio.run(fetch_world_state_snapshot(rs, max_chars=90))
    assert out and len(out) <= 90
    assert out.endswith("…")

"""P3 — observabilité honnête.

Bug réel (mission `4a810e6e`, 29/10) : une perception qui répond « non. On voit
actuellement Qt Creator… » était badgée « trouvé », parce que le badge ne
regardait que des marqueurs d'absence anglo-français incomplets et ignorait le
`result` structuré. Idem pour les tokens : le header affichait le total de la
mission sans le distinguer de la session.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_observability_report import (
    _analysis_reports_absence,
    attach_llm_calls_by_mission,
    attach_usage_summaries,
)


def _ep(mid="m1", sid="s1", session="sess1"):
    return {"mission_id": mid, "session_id": session, "execution_tree": {"solver_id": sid}}


def _perceive_call(resp, mid="m1", sid="s1"):
    return {
        "tag": "perceive_understand",
        "mission_id": mid,
        "solver_id": sid,
        "response": resp,
        "ts": "2026-10-29T00:21:14",
    }


def test_found_uses_structured_result():
    ep = _ep()
    attach_llm_calls_by_mission(
        [ep], [_perceive_call({"result": False, "data": "Paint est ouvert"})], []
    )
    assert ep["_analysis_calls"][0]["found"] is False


def test_positive_structured_result_not_overridden_by_text():
    ep = _ep()
    attach_llm_calls_by_mission(
        [ep], [_perceive_call({"result": True, "data": "Paint est ouvert"})], []
    )
    assert ep["_analysis_calls"][0]["found"] is True


def test_negative_answer_not_badged_found():
    assert _analysis_reports_absence("non. On voit actuellement Qt Creator") is True
    assert _analysis_reports_absence("Paint n'est pas ouvert") is True
    assert _analysis_reports_absence("Paint is not open") is True
    assert _analysis_reports_absence("Paint est ouvert") is False


def test_mission_and_session_tokens_are_labeled():
    eps = [_ep("m1", "s1", "sess1"), _ep("m2", "s2", "sess1")]
    calls = [
        {"tag": "Plan", "mission_id": "m1",
         "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                   "total_tokens": 120, "source": "real"}},
        {"tag": "Plan", "mission_id": "m2",
         "usage": {"prompt_tokens": 200, "completion_tokens": 30,
                   "total_tokens": 230, "source": "real"}},
    ]
    attach_usage_summaries(eps, calls)
    assert eps[0]["_usage"]["total_tokens"] == 120
    assert eps[0]["_session_usage"]["total_tokens"] == 350
    assert eps[0]["_session_usage"]["missions"] == 2
    assert eps[1]["_session_usage"]["total_tokens"] == 350


def test_header_labels_mission_and_session():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "build_observability_report.py",
    )
    src = open(path, encoding="utf-8").read()
    assert "Tokens mission" in src
    assert "Session" in src


# --------------------------------------------------------------------------
# P3.3 — le world state doit exister dans l'export
# --------------------------------------------------------------------------

def test_world_snapshot_events_attached():
    from tools.build_observability_report import attach_world_snapshot_events

    eps = [_ep("m1", "s1"), _ep("m2", "s2")]
    events = [
        {"event": "world_snapshot", "mission_id": "m1", "chars": 120},
        {"event": "world_snapshot", "solver_id": "s2", "chars": 80},
        {"event": "other", "mission_id": "m1"},
    ]
    attach_world_snapshot_events(eps, events)
    assert len(eps[0]["_world_snapshots"]) == 1
    assert len(eps[1]["_world_snapshots"]) == 1


def test_snapshot_event_emitted(monkeypatch):
    import asyncio
    import types
    from utils.logger import Logger
    from core.alignment import fetch_world_state_snapshot

    captured = []

    def fake_event(name, **fields):
        captured.append((name, fields))

    monkeypatch.setattr(Logger, "event", staticmethod(fake_event))

    class FakeTM:
        def known_tool_names(self):
            return {"get_world_state"}

        async def execute_tool(self, name, args):
            return json.dumps({"result": True, "data": {"summary_text": "Paint"}})

    rs = types.SimpleNamespace(
        tools_manager=FakeTM(), host_manifest=None,
        execution_context={"mission_id": "m1", "solver_id": "s1"},
    )
    asyncio.run(fetch_world_state_snapshot(rs, max_chars=400))

    names = [n for n, _ in captured]
    assert "world_snapshot" in names
    fields = dict(captured)["world_snapshot"]
    assert fields["mission_id"] == "m1"
    assert fields["source_tool"] == "get_world_state"
    assert fields["chars"] > 0
    assert fields["hash"]


def test_export_source_renders_world_snapshots():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "build_observability_report.py",
    )
    src = open(path, encoding="utf-8").read()
    assert "World state" in src
    assert "_world_snapshots" in src


def test_finished_at_est_stocke_en_utc_naif():
    """`created_at` (SQLite) est UTC : `finished_at` doit l'être aussi."""
    from datetime import datetime, timedelta, timezone

    from memory.mission_store import utc_naive

    aware = datetime(2026, 11, 4, 12, 30, tzinfo=timezone(timedelta(hours=1)))
    stored = utc_naive(aware)
    assert stored.tzinfo is None
    assert stored == datetime(2026, 11, 4, 11, 30)
    assert utc_naive(None) is None

    # Valeur naïve historique = heure locale : convertie, jamais recopiée.
    local_naive = datetime(2026, 11, 4, 12, 30)
    converted = utc_naive(local_naive)
    assert converted.tzinfo is None
    expected = local_naive.astimezone(timezone.utc).replace(tzinfo=None)
    assert converted == expected


def test_rapport_interprete_les_horodatages_naifs_comme_utc():
    """Sans marqueur, le navigateur lirait l'heure locale : le rapport force UTC."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "build_observability_report.py",
    )
    src = open(path, encoding="utf-8").read()
    assert "+ 'Z'" in src
    assert "replace(' ', 'T')" in src

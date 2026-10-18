"""BUG D (P1) — observabilité : sink JSONL intégral, stderr court.

Le sink JSONL doit porter l'enregistrement COMPLET (post-mortem) même quand le
résumé stderr est tronqué à 300 caractères. Le chemin du sink est stable
(data dir) et surchargeable par `MANAGENT_EVENTS_SINK`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import Logger


def test_sink_integral_stderr_court(tmp_path, capsys):
    sink = tmp_path / "observability" / "events.jsonl"
    previous = Logger._json_sink_path
    try:
        Logger.configure_json_sink(str(sink))
        big = "x" * 1000
        Logger.event("test.event", payload=big)

        assert sink.is_file()
        line = sink.read_text(encoding="utf-8").strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "test.event"
        assert record["payload"] == big

        err = capsys.readouterr().err
        assert "[tronqué stderr]" in err
        assert big not in err
    finally:
        Logger._json_sink_path = previous


def test_configure_cree_le_dossier(tmp_path):
    sink = tmp_path / "deep" / "nested" / "events.jsonl"
    previous = Logger._json_sink_path
    try:
        Logger.configure_json_sink(str(sink))
        assert sink.parent.is_dir()
    finally:
        Logger._json_sink_path = previous

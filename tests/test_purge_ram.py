"""Purge totale : la RAM suit SQLite (rapide, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.runtime_state import RuntimeState


def _dirty(rs):
    rs.llm_usage = {"by_mission": {"m1": {"total_tokens": 99}}, "total": {"total_tokens": 99, "calls": 1}}
    rs.truncation_count = {"Plan": 2}
    rs.waiting_stats = {"defaut": 3}


def test_reset_usage_counters_zeroes_ram():
    rs = RuntimeState.__new__(RuntimeState)
    _dirty(rs)
    rs.reset_usage_counters()
    assert rs.llm_usage == {"by_mission": {}, "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}}
    assert rs.truncation_count == 0
    assert rs.waiting_stats == {}


def test_reset_usage_counters_never_raises():
    class ExplodingRS:
        @property
        def llm_usage(self):
            raise RuntimeError("boom")

        @llm_usage.setter
        def llm_usage(self, value):
            raise RuntimeError("boom")

    RuntimeState.reset_usage_counters(ExplodingRS())

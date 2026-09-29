"""Compteurs tokens : normalisation, cumul, requêtes. Nos yeux auto + tes tests manuels."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.base_provider import BaseProvider
from tools.build_observability_report import attach_usage_summaries


def test_normalize_openai_usage():
    u = BaseProvider.normalize_openai_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert u == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"}


def test_normalize_openai_usage_missing_returns_none():
    assert BaseProvider.normalize_openai_usage({}) is None
    assert BaseProvider.normalize_openai_usage(None) is None


def test_normalize_anthropic_usage():
    u = BaseProvider.normalize_anthropic_usage({"input_tokens": 7, "output_tokens": 3})
    assert u == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "source": "real"}


def test_normalize_gemini_usage_object():
    class FakeMeta:
        prompt_token_count = 12
        candidates_token_count = 8
        total_token_count = 20
    u = BaseProvider.normalize_gemini_usage(FakeMeta())
    assert u["source"] == "real" and u["total_tokens"] == 20


def test_estimate_usage_labelled_estimated():
    u = BaseProvider.estimate_usage("x" * 40, "y" * 40)
    assert u["source"] == "estimated" and u["total_tokens"] == 20


def test_usage_summary_per_mission_with_sources():
    eps = [
        {"mission_id": "m1", "execution_tree": {"solver_id": "m1", "attempts": []}},
        {"mission_id": "m2", "execution_tree": {"solver_id": "m2", "attempts": []}},
    ]
    calls = [
        {"mission_id": "m1", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"}},
        {"mission_id": "m1", "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10, "source": "estimated"}},
        {"mission_id": "m2", "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8, "source": "real"}},
        {"mission_id": "m1"},  # sans usage = ignoré, jamais inventé
    ]
    attach_usage_summaries(eps, calls)
    s1 = eps[0]["_usage"]
    assert s1["total_tokens"] == 25 and s1["calls"] == 2
    assert s1["real_calls"] == 1 and s1["estimated_calls"] == 1
    assert eps[1]["_usage"]["total_tokens"] == 8


def test_llm_record_usage_cumulates():
    from core.llm import Llm

    class FakeRS:
        pass
    llm = Llm.__new__(Llm)
    llm.runtime_state = FakeRS()
    llm._record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"}, "m1")
    llm._record_usage({"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5, "source": "estimated"}, "m1")
    store = llm.runtime_state.llm_usage
    assert store["by_mission"]["m1"]["total_tokens"] == 20
    assert store["total"]["calls"] == 2

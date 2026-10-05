"""Usage tokens persistant : survit au redémarrage (rapide, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.usage_store import UsageStore


def _db(tmp_path):
    return str(tmp_path / "usage_test.db")


def test_fresh_db_loads_empty(tmp_path):
    assert UsageStore(_db(tmp_path)).load_all() == {}


def test_record_then_reload_survives_restart(tmp_path):
    path = _db(tmp_path)
    UsageStore(path).record("m1", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"})
    UsageStore(path).record("m1", {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5, "source": "estimated"})
    reloaded = UsageStore(path).load_all()
    assert reloaded["m1"]["total_tokens"] == 20
    assert reloaded["m1"]["calls"] == 2
    assert reloaded["m1"]["real_calls"] == 1
    assert reloaded["m1"]["estimated_calls"] == 1


def test_record_uses_global_when_no_mission(tmp_path):
    path = _db(tmp_path)
    UsageStore(path).record("", {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4, "source": "real"})
    assert "global" in UsageStore(path).load_all()


def test_clear_all_empties(tmp_path):
    path = _db(tmp_path)
    UsageStore(path).record("m1", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "source": "real"})
    UsageStore(path).clear_all()
    assert UsageStore(path).load_all() == {}


def test_llm_record_writes_through_to_db(tmp_path):
    from core.llm import Llm

    class FakeRS:
        pass
    rs = FakeRS()
    rs.usage_store = UsageStore(_db(tmp_path))
    llm = Llm.__new__(Llm)
    llm.runtime_state = rs
    llm._record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"}, "m1")
    assert rs.llm_usage["by_mission"]["m1"]["total_tokens"] == 15
    assert UsageStore(rs.usage_store.db_path).load_all()["m1"]["total_tokens"] == 15


def test_llm_record_without_store_still_works_in_ram():
    from core.llm import Llm

    class FakeRS:
        pass
    llm = Llm.__new__(Llm)
    llm.runtime_state = FakeRS()
    llm._record_usage({"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "source": "real"}, "m9")
    assert llm.runtime_state.llm_usage["by_mission"]["m9"]["calls"] == 1


def test_reload_rebuilds_total_like_startup(tmp_path):
    store = UsageStore(_db(tmp_path))
    store.record("m1", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "real"})
    store.record("m2", {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8, "source": "real"})
    # Même reconstruction que Orchestrator._load_persisted_usage au démarrage.
    by_mission = UsageStore(store.db_path).load_all()
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    for entry in by_mission.values():
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            total[key] += int(entry.get(key) or 0)
    assert by_mission["m1"]["total_tokens"] == 15
    assert total["total_tokens"] == 23
    assert total["calls"] == 2

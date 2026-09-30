"""Plafonds PD reglables : defauts, override hote, bornes. Nos yeux auto."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import (
    clamp_discovery_limit,
    discovery_limits_from_runtime,
    LLM_DISCOVERY_MAX_ITERATIONS,
    DISCOVERY_MAX_ITERATIONS,
)


def test_defaults_without_runtime():
    loop, sess, ls, ss = discovery_limits_from_runtime(None)
    assert (loop, sess) == (LLM_DISCOVERY_MAX_ITERATIONS, DISCOVERY_MAX_ITERATIONS)
    assert (ls, ss) == ("defaut", "defaut")


def test_host_override_wins():
    rs = types.SimpleNamespace(discovery_max_iterations=8, discovery_max_session_steps=12)
    loop, sess, ls, ss = discovery_limits_from_runtime(rs)
    assert (loop, sess) == (8, 12)
    assert (ls, ss) == ("hôte", "hôte")


def test_bounds_clamped_to_hard_max():
    assert clamp_discovery_limit(100, 5) == (20, "hôte")
    assert clamp_discovery_limit(0, 5) == (1, "hôte")
    assert clamp_discovery_limit("nawak", 5) == (5, "defaut")
    assert clamp_discovery_limit(None, 10) == (10, "defaut")


def test_partial_override_keeps_other_default():
    rs = types.SimpleNamespace(discovery_max_iterations=7)
    loop, sess, ls, ss = discovery_limits_from_runtime(rs)
    assert loop == 7 and ls == "hôte"
    assert sess == DISCOVERY_MAX_ITERATIONS and ss == "defaut"


def _fake_plan():
    from core.discovery.models import DiscoveryPlan
    return DiscoveryPlan(
        signature="sig:test", goal="g", data_type="world",
        targets=["t"], technical_goals=["tg"], steps=[],
    )


def test_session_uses_runtime_limit():
    from core.discovery.discovery_session import DiscoverySession

    class FakeCtx:
        def get(self, k, d=None):
            return d

    rs = types.SimpleNamespace(
        discovery_max_session_steps=12,
        execution_context=FakeCtx(),
        discovery_llm=None,
        discovery_engine=None,
    )
    s = DiscoverySession(entity_id="e1", plan=_fake_plan(), explorer=None,
                         runtime_state=rs, llm=None)
    assert s.max_iterations == 12


def test_session_falls_back_to_default():
    from core.discovery.discovery_session import DiscoverySession

    class FakeCtx:
        def get(self, k, d=None):
            return d

    rs = types.SimpleNamespace(execution_context=FakeCtx(), discovery_llm=None,
                               discovery_engine=None)
    s = DiscoverySession(entity_id="e1", plan=_fake_plan(), explorer=None,
                         runtime_state=rs, llm=None)
    assert s.max_iterations == DISCOVERY_MAX_ITERATIONS

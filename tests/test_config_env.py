"""Tests résolution env: (purs, rapides, sans secrets)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import resolve_env_refs, resolve_payload_env, resolve_hitl_policy


def test_resolves_simple_ref(monkeypatch):
    monkeypatch.setenv("T_KEY", "abc123")
    assert resolve_env_refs("env:T_KEY") == "abc123"


def test_missing_var_gives_empty_and_reports():
    missing = []
    assert resolve_env_refs("env:NOPE_ABSENT_VAR", missing) == ""
    assert missing == ["NOPE_ABSENT_VAR"]


def test_nested_lists_rotation(monkeypatch):
    monkeypatch.setenv("K1", "k-1")
    monkeypatch.setenv("K2", "k-2")
    payload = {"api_keys": {"gemini": "env:K1"}, "keys": ["env:K1", "env:K2", "plain"]}
    out, missing = resolve_payload_env(payload)
    assert out == {"api_keys": {"gemini": "k-1"}, "keys": ["k-1", "k-2", "plain"]}
    assert missing == []


def test_non_strings_untouched():
    assert resolve_env_refs({"n": 3, "b": True, "x": None}) == {"n": 3, "b": True, "x": None}


def test_runtime_stamp_shape():
    from utils.paths import runtime_stamp
    st = runtime_stamp()
    assert set(st) == {"managent_version", "git_commit", "dirty", "frozen"}
    assert isinstance(st["dirty"], bool) and isinstance(st["frozen"], bool)


def test_hitl_top_level_wins_and_nested_fallback():
    assert resolve_hitl_policy({"hitl_policy": "strict"}) == "strict"
    assert resolve_hitl_policy({"runtime_configuration": {"hitl_policy": "autonomous"}}) == "autonomous"
    assert resolve_hitl_policy({"hitl_policy": "strict",
                                "runtime_configuration": {"hitl_policy": "autonomous"}}) == "strict"
    assert resolve_hitl_policy({}) == "balanced"
    assert resolve_hitl_policy({"hitl_policy": "n’importe quoi"}) == "balanced"

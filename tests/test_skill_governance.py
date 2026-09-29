"""Gouvernance skills : defauts, 3 niveaux, banque stricte, demo souple. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import SKILL_GOVERNANCE_DEFAULTS
from core.skills.governance import (
    resolve_skill_governance,
    tier_for_tools,
    tool_sensitivity_map,
    tool_requires_env_map,
    canonical_skill_id,
    legacy_skill_id,
    normalize_tier,
)


def test_defaults_match_contract():
    v, s = resolve_skill_governance("skill.x.y")
    assert v["discovery_threshold"] == 2
    assert v["shadow_success_threshold"] == 1
    assert v["shadow_mismatch_threshold"] == 3
    assert v["circuit_breaker_max_failures"] == 3
    assert v["max_repairs"] == 3
    assert v["champion_margin"] == 0.05
    assert set(s.values()) == {"defaut"}
    assert set(v) == set(SKILL_GOVERNANCE_DEFAULTS)


def test_host_global_override_wins_with_source():
    v, s = resolve_skill_governance(
        "skill.x.y", host_governance={"circuit_breaker_max_failures": 1})
    assert v["circuit_breaker_max_failures"] == 1
    assert s["circuit_breaker_max_failures"] == "hôte"
    assert s["discovery_threshold"] == "defaut"


def test_bank_strict_halves_thresholds():
    v, s = resolve_skill_governance("skill.virement.envoyer", tier="strict")
    assert v["circuit_breaker_max_failures"] == 1  # 3 // 2
    assert v["discovery_threshold"] == 1
    assert v["max_repairs"] == 1
    assert s["circuit_breaker_max_failures"] == "sensibilité"


def test_demo_relaxed_doubles():
    v, _ = resolve_skill_governance("skill.x.y", tier="relaxed")
    assert v["circuit_breaker_max_failures"] == 6
    assert v["shadow_success_threshold"] == 2


def test_per_skill_wins_over_everything():
    v, s = resolve_skill_governance(
        "skill.virement.envoyer", tier="strict",
        host_governance={"circuit_breaker_max_failures": 5,
                         "skills": {"skill.virement.envoyer": {"circuit_breaker_max_failures": 1}}})
    assert v["circuit_breaker_max_failures"] == 1
    assert s["circuit_breaker_max_failures"] == "skill"


def test_explicit_host_value_beats_tier_math():
    v, s = resolve_skill_governance(
        "skill.x.y", tier="strict",
        host_governance={"circuit_breaker_max_failures": 5})
    assert v["circuit_breaker_max_failures"] == 5
    assert s["circuit_breaker_max_failures"] == "hôte"


def test_infinite_repairs_preserved():
    v, s = resolve_skill_governance(
        "skill.x.y", tier="strict", host_governance={"max_repairs": None})
    assert v["max_repairs"] is None
    v2, _ = resolve_skill_governance("skill.x.y", tier="relaxed")
    assert v2["max_repairs"] == 6


def test_bad_values_clamped():
    v, _ = resolve_skill_governance(
        "skill.x.y", host_governance={"circuit_breaker_max_failures": 0,
                                      "champion_margin": 42})
    assert v["circuit_breaker_max_failures"] == 1
    assert v["champion_margin"] == 1.0


def test_tier_for_tools_max_wins():
    sens = {"a": "normal", "b": "high", "c": "low"}
    assert tier_for_tools(["a", "c"], sens) == "relaxed"
    assert tier_for_tools(["a", "b", "c"], sens) == "strict"
    assert tier_for_tools(["a"], sens) == "standard"
    assert tier_for_tools([], sens) == "standard"


def test_tool_maps_from_manifest():
    tools = [{"name": "virement.envoyer", "sensitivity": "high", "requires_env": ["session_auth"]},
             {"name": "list_files"}]
    assert tool_sensitivity_map(tools) == {"virement.envoyer": "high", "list_files": "normal"}
    assert tool_requires_env_map(tools) == {"virement.envoyer": ["session_auth"]}


def test_skill_ids_neutral_with_legacy_fallback():
    assert canonical_skill_id("press", "run dialog box") == "skill.press.run_dialog_box"
    assert legacy_skill_id("press", "run dialog box") == "desktop.press.run_dialog_box"
    assert canonical_skill_id("", "x") is None


def test_legacy_low_stays_standard():
    assert normalize_tier("low") == "standard"
    v, _ = resolve_skill_governance("skill.x.y", tier="low")
    assert v["circuit_breaker_max_failures"] == 3

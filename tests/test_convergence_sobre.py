"""Convergence sobre : juge isole, preuves compactes, recul et resignation. Nos yeux auto."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel
from core.llm import Llm
from core.solver import _update_tool_health


class _FakeSchema(BaseModel):
    reponse: str = ""


class _FakeProvider:
    def __init__(self, data_type, targets):
        self._data_type = data_type
        self._targets = targets

    def get_data_type(self):
        return self._data_type

    def get_targets(self):
        return self._targets

    def get_scope_description(self):
        return "bac"


class _FakeExplorer:
    def get_available_goals(self):
        return ["lire"]


class _FakeEngine:
    def get_explorer(self, data_type):
        return _FakeExplorer()


class _FakeEntity:
    entity_id = "ent"
    name = "Ent"
    role = "test"

    def __init__(self, providers):
        self._providers = providers

    def get_data_providers(self):
        return self._providers

    def get_data_context(self):
        return None


def _llm(allowlist):
    llm = Llm(provider_manager=None, provider_id="f", model_id="f",
              system_prompt="", runtime_state=None)
    llm.enable_discovery(_FakeEngine(), _FakeEntity({
        "world": _FakeProvider("world", ["w"]),
        "registry": _FakeProvider("registry", ["r"]),
        "missions": _FakeProvider("missions", ["m"]),
    }), allowed_data_types=allowlist)
    return llm


def test_allowlist_stored_and_cleared():
    llm = _llm({"world", "registry"})
    assert llm._discovery_allowlist == {"world", "registry"}
    llm.disable_discovery()
    assert llm._discovery_allowlist is None


def test_build_section_keeps_only_allowed():
    llm = _llm({"world", "registry"})
    section = llm._build_discovery_section(_FakeSchema, set())
    assert "world" in section
    assert "registry" in section
    assert "missions" not in section


def test_build_section_full_without_allowlist():
    llm = _llm(None)
    section = llm._build_discovery_section(_FakeSchema, set())
    assert "missions" in section


def _node(tool, status):
    return types.SimpleNamespace(step_id="s", tool_name=tool, status=status)


def _att(tool, status):
    return types.SimpleNamespace(outcome="failed" if status == "failed" else "success",
                                 nodes=[_node(tool, status)])


def test_tool_health_sick_after_three_straight():
    streak = {}
    atts = []
    for _ in range(2):
        atts.append(_att("click_at", "failed"))
        assert _update_tool_health(streak, atts) == []
    atts.append(_att("click_at", "failed"))
    assert _update_tool_health(streak, atts) == ["click_at"]


def test_tool_health_resets_on_change():
    streak = {"click_at": 2}
    atts = [_att("click_at", "failed"), _att("press_key", "failed")]
    assert _update_tool_health(streak, atts) == []
    assert streak["click_at"] == 0
    assert streak["press_key"] == 1


def test_convergence_template_has_tool_status():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en"):
        out = get_prompt_loader().load(
            "convergence.md", lang=lang, step_description="d",
            expected_result="e", actual_result="r", tool_status="OK")
        assert "OK" in out and "{{" not in out


def test_analyze_prompt_success_is_technical():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "en"):
        text = open(os.path.join(base, "prompts", lang, "llm_analyze_data.md"),
                    encoding="utf-8").read()
        assert "technique" in text

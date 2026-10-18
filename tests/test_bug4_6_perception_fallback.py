"""Lot B (BUG-4/6) : canal de repli perception + alerte critère mécanique."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import internal_tools as IT
from core.tools_models import AnalysisResult
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import (
    PlanValidator,
    find_mechanical_perception_criteria,
)


class _FakeLLM:
    def __init__(self, res):
        self._res = res

    async def generate_structured(self, prompt=None, schema=None, tag="",
                                   media_assets=None, mission_id=None):
        return self._res


class _FakeMgr:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def execute_tool(self, name, args, llm=None):
        self.calls.append((name, args))
        return json.dumps(self._payload)


def _fake_state(payload):
    return SimpleNamespace(
        tools_manager=_FakeMgr(payload),
        language="en",
    )


# --------------------------------------------------------------------------
# Canal de repli : l'info perçue survit dans `data` même en échec
# --------------------------------------------------------------------------

def test_analyze_failure_keeps_description_in_data(monkeypatch):
    async def _fake_llm(_state):
        return _FakeLLM(AnalysisResult(
            success=False, data=None, error_reason=None,
            message="Écran visible : barre latérale, bouton OK, cible absente.",
        ))

    monkeypatch.setattr(IT, "_get_tools_llm", _fake_llm)
    out = asyncio.run(IT._run_llm_analysis(
        "brut", "que voit-on ?", _fake_state({}), tag="llm_analyze_data"))
    assert out["result"] is False
    assert "barre latérale" in str(out["data"])


def test_analyze_failure_keeps_existing_data(monkeypatch):
    async def _fake_llm(_state):
        return _FakeLLM(AnalysisResult(
            success=False, data="déjà rempli", error_reason=None, message="m",
        ))

    monkeypatch.setattr(IT, "_get_tools_llm", _fake_llm)
    out = asyncio.run(IT._run_llm_analysis(
        "brut", "q", _fake_state({}), tag="llm_analyze_data"))
    assert out["data"] == "déjà rempli"


def test_perceive_understand_source_failure_keeps_partial_data():
    st = _fake_state({
        "result": False,
        "error_reason": "capture ratée",
        "data": {"hint": "écran noir"},
    })
    out = asyncio.run(IT.perceive_understand(
        {"question": "que voir ?", "source_tool": "vision", "source_args": {}},
        st))
    assert out["result"] is False
    assert out["data"] == {"hint": "écran noir"}


def test_perceive_understand_source_failure_message_as_data():
    st = _fake_state({"result": False, "error_reason": "capteur indisponible"})
    out = asyncio.run(IT.perceive_understand(
        {"question": "que voir ?", "source_tool": "vision", "source_args": {}},
        st))
    assert out["result"] is False
    assert out["data"] == "capteur indisponible"


def test_strict_format_relaxed_on_absence(monkeypatch):
    seen = {}

    async def fake_run(data, query, runtime_state, tag="", media_assets=None):
        seen["q"] = query
        return {"result": False, "data": "écran sans la cible", "error_reason": "absente"}

    monkeypatch.setattr(IT, "_run_llm_analysis", fake_run)
    st = _fake_state({"result": True, "data": {"scan": [1]}})
    out = asyncio.run(IT.perceive_understand(
        {"question": "le bouton est-il là ?", "source_tool": "vision",
         "source_args": {}, "format_response": "yes or no"}, st))
    assert "yes or no" in seen["q"]          # format strict conservé en cas de succès
    assert "cible absente" in seen["q"]      # dérogation décrite si introuvable
    assert out["data"] == "écran sans la cible"


# --------------------------------------------------------------------------
# Alerte critère mécanique sur étape de perception (jamais un refus)
# --------------------------------------------------------------------------

def _step(sid, tool, expected):
    return PlanStep(id=sid, description="d", type=StepType.TOOL_CALL,
                    tool_name=tool, tool_args_json=json.dumps(
                        {"question": "q ?", "source_tool": "vision", "source_args": {}}),
                    expected_result=expected)


def test_mechanical_criteria_flagged_on_perception_only():
    plan = Plan(goal="g", steps=[
        _step("s1", "perceive_understand", "true"),
        _step("s2", "vision", "any"),
    ])
    flagged = find_mechanical_perception_criteria(plan, {"vision"})
    assert flagged == ["s1: expected_result='true' (perceive_understand)",
                       "s2: expected_result='any' (vision)"]


def test_semantic_criterion_not_flagged():
    plan = Plan(goal="g", steps=[
        _step("s1", "perceive_understand", "le prix affiché est 42"),
    ])
    assert find_mechanical_perception_criteria(plan, None) == []


def test_note_and_non_perception_untouched():
    plan = Plan(goal="g", steps=[
        _step("s1", "perceive_understand", "true"),
        PlanStep(id="s2", description="cliquer", type=StepType.TOOL_CALL,
                 tool_name="click", tool_args_json="{}", expected_result="true"),
    ])
    assert find_mechanical_perception_criteria(plan, None) == [
        "s1: expected_result='true' (perceive_understand)"]
    note = PlanValidator.build_mechanical_perception_criteria_note(plan, None)
    assert note and "s1" in note

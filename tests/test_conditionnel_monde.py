"""Champs-références jamais interpolés + guidage monde conditionnel (rapide, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.alignment import world_guidance_visible


def _ex():
    return Executor.__new__(Executor)


def test_reference_fields_kept_raw():
    ex = _ex()
    ex.solver = type("S", (), {"variable_registry": {
        "data_step_1": {"value": "CONTENU", "description": "d", "source": "s"},
    }})()
    out = ex._interpolate_dict(
        {"source_data": "$@_data_step_1", "question": "q $@_data_step_1"},
        for_json=True,
        skip_keys={"source_data", "source", "sources"},
    )
    assert out["source_data"] == "$@_data_step_1"
    assert "CONTENU" in out["question"]


def test_interpolation_normale_sans_skip():
    ex = _ex()
    ex.solver = type("S", (), {"variable_registry": {
        "data_step_1": {"value": "CONTENU", "description": "d", "source": "s"},
    }})()
    out = ex._interpolate_dict({"q": "$@_data_step_1"}, for_json=True)
    assert "CONTENU" in out["q"]


def test_guidance_visible_par_defaut():
    assert world_guidance_visible(None) is True
    assert world_guidance_visible([]) is True
    assert world_guidance_visible([
        {"name": "a", "kind": "action"},
    ]) is True


def test_guidance_masquee_si_tout_deterministe():
    tools = [
        {"name": "a1", "kind": "action", "effects": "deterministic"},
        {"name": "a2", "kind": "action", "effects": "deterministic"},
    ]
    assert world_guidance_visible(tools) is False


def test_guidance_visible_si_perception():
    tools = [
        {"name": "a1", "kind": "action", "effects": "deterministic"},
        {"name": "p1", "kind": "perception"},
    ]
    assert world_guidance_visible(tools) is True


def test_planner_masque_sans_monde():
    from core.prompt_loader import get_prompt_loader
    tools = [{"name": "x", "kind": "action", "description": "d",
              "parameters": {"type": "object", "properties": {}}}]
    out = get_prompt_loader().load(
        "planner.md", lang="base", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, tools=tools, skills="",
        model_id="m", supported_modalities=[], unsupported_modalities=[],
        world_guidance=False)
    assert "{{" not in out
    assert "LIRE LE MONDE" not in out
    assert "HARNAIS" in out


def test_planner_montre_par_defaut():
    from core.prompt_loader import get_prompt_loader
    tools = [{"name": "x", "kind": "action", "description": "d",
              "parameters": {"type": "object", "properties": {}}}]
    out = get_prompt_loader().load(
        "planner.md", lang="base", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, tools=tools, skills="",
        model_id="m", supported_modalities=[], unsupported_modalities=[])
    assert "LIRE LE MONDE" in out and "perceive_understand" in out


def test_planner_echecs_dedies():
    from core.prompt_loader import get_prompt_loader
    tools = [{"name": "x", "kind": "action", "description": "d",
              "parameters": {"type": "object", "properties": {}}}]
    without = get_prompt_loader().load(
        "planner.md", lang="en", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, tools=tools, skills="",
        model_id="m", supported_modalities=[], unsupported_modalities=[])
    assert "PAST FAILURES" not in without
    with_fail = get_prompt_loader().load(
        "planner.md", lang="en", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, tools=tools, skills="",
        model_id="m", supported_modalities=[], unsupported_modalities=[],
        previous_failures="- tentative 1 : boom")
    assert "boom" in with_fail

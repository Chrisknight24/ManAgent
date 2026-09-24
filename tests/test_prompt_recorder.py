"""Tests recorder prompts (opt-in, noms de variables seuls)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import PromptLoader

def test_records_rendered_prompt_and_var_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MANAGENT_RECORD_PROMPTS", "1")
    pl = PromptLoader()
    out = pl.load("convergence.md", lang="base", goal="g")
    assert len(out) > 0
    files = sorted(os.listdir(tmp_path / "prompts_log"))
    assert any(f.endswith(".md") for f in files)
    assert any(f.endswith(".vars.txt") for f in files)


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANAGENT_RECORD_PROMPTS", raising=False)
    pl = PromptLoader()
    pl.load("convergence.md", lang="base", goal="g")
    assert not (tmp_path / "prompts_log").exists()


def test_kinds_legend_single_source():
    pl = PromptLoader()
    for lang in ("base", "fr", "en"):
        text = pl.load("_kinds_legend.md", lang=lang)
        assert "perceive_understand" in text
        assert "human_validation" in text


def test_planner_renders_kind_tags():
    pl = PromptLoader()
    tools = [{"name": "lidar_scan", "kind": "perception",
              "description": "scan", "parameters": {}}]
    out = pl.load("planner.md", lang="base", goal="g", strategy="s",
                  context="", variable_registry={}, tools=tools,
                  skills="", advice="", model_id="m",
                  supported_modalities=[], unsupported_modalities=[])
    assert "[perception]" in out
    assert "lidar_scan" in out

"""Alignement capteur-vs-juge : effets, risque, nudges (pur, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import (
    effect_of,
    uncertain_names,
    toolset_nudge,
    plan_risk,
    risk_sentence,
    planner_nudge,
    allowed_values,
    invalid_enum_value,
)


def test_effect_defaults_prudents():
    assert effect_of({"name": "a", "kind": "action"}) == "uncertain"
    assert effect_of({"name": "a"}) == "uncertain"
    assert effect_of({"name": "p", "kind": "perception"}) == "none"
    assert effect_of({"name": "u", "kind": "utility"}) == "none"
    assert effect_of({"name": "a", "kind": "action", "effects": "deterministic"}) == "deterministic"
    assert effect_of({"name": "a", "kind": "action", "effects": "nawak"}) == "uncertain"
    assert effect_of({}) == "uncertain"  # sans kind = action par défaut = prudent
    assert effect_of(None) == "none"


def test_uncertain_names_que_externes():
    tools = [
        {"name": "a1", "kind": "action"},
        {"name": "a2", "kind": "action", "effects": "deterministic"},
        {"name": "p1", "kind": "perception"},
        {"name": "i1", "kind": "action", "source": "internal"},
        {"name": "", "kind": "action"},
    ]
    assert uncertain_names(tools) == {"a1"}


def test_toolset_nudge_vide_si_stable():
    assert toolset_nudge([]) == ""
    assert toolset_nudge([{"name": "a", "kind": "action", "effects": "deterministic"}]) == ""
    assert toolset_nudge([{"name": "p", "kind": "perception"}]) == ""


def test_toolset_nudge_incite_si_incertain():
    nudge = toolset_nudge([
        {"name": "a1", "kind": "action"},
        {"name": "a2", "kind": "action"},
        {"name": "p", "kind": "perception"},
    ])
    assert nudge != "" and "2/2" in nudge


def test_plan_risk_bas_si_couvert():
    risk = plan_risk(
        ["a1", "a2", "perceive_understand"], {"a1", "a2"}, set())
    assert risk["level"] == "low" and risk["uncovered"] == 0


def test_plan_risk_haut_si_aveugle():
    risk = plan_risk(["a1", "a2", "a3"], {"a1", "a2", "a3"}, set())
    assert risk["level"] == "high" and risk["uncovered"] == 3


def test_plan_risk_compte_depuis_derniere_lecture():
    risk = plan_risk(
        ["a1", "sense_host", "a2"], {"a1", "a2"}, {"sense_host"})
    assert risk["uncovered"] == 1 and risk["total_uncertain"] == 2


def test_risk_sentence_vide_si_bas():
    assert risk_sentence({"level": "low", "uncovered": 0}) == ""
    assert "2" in risk_sentence({"level": "high", "uncovered": 2})


def test_planner_nudge_credit():
    assert planner_nudge(["a1", "perceive_understand"], {"a1"}, set()) == ""
    assert planner_nudge(["a1"], set(), set()) == ""
    nudge = planner_nudge(["a1", "a2"], {"a1", "a2"}, set())
    assert "a1" in nudge and "a2" in nudge


def test_planner_prompt_rend_sans_et_avec_effects():
    from core.prompt_loader import get_prompt_loader

    def _tools(with_effects):
        base = [{"name": "x", "kind": "action", "description": "d",
                 "parameters": {"type": "object", "properties": {}}}]
        if with_effects:
            base[0]["effects"] = "uncertain"
        return base

    for lang in ("base", "en", "fr"):
        plain = get_prompt_loader().load(
            "planner.md", lang=lang, goal="g", strategy="s", context="c",
            advice="", variable_registry={}, tools=_tools(False), skills="",
            model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "{{" not in plain and "perceive_understand" in plain
        marked = get_prompt_loader().load(
            "planner.md", lang=lang, goal="g", strategy="s", context="c",
            advice="", variable_registry={}, tools=_tools(True), skills="",
            model_id="m", supported_modalities=[], unsupported_modalities=[])
        assert "uncertain" in marked


def test_convergence_rend_avec_et_sans_note():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en", "fr"):
        without = get_prompt_loader().load(
            "convergence.md", lang=lang, step_description="d",
            expected_result="e", actual_result="r", tool_status="OK")
        assert "{{" not in without
        with_note = get_prompt_loader().load(
            "convergence.md", lang=lang, step_description="d",
            expected_result="e", actual_result="r", tool_status="OK",
            alignment_note="Fait alignement : 2 actions.")
        assert "2 actions" in with_note


def test_enum_valeurs_declarees():
    tool = {"name": "v", "kind": "perception",
            "parameters": {"properties": {"mode": {"enum": ["image", "annotated"]}},
                           "required": ["mode"]}}
    assert allowed_values(tool, "mode") == ["image", "annotated"]
    assert allowed_values(tool, "query") is None
    assert allowed_values({}, "mode") is None
    assert invalid_enum_value(tool, {"mode": "image"}) is None
    bad = invalid_enum_value(tool, {"mode": "capture_screenshot"})
    assert bad is not None and "image" in bad
    assert invalid_enum_value(tool, {"query": "libre"}) is None


def test_manager_expose_incertains():
    from tools.tools_manager import ToolsManager
    import asyncio

    async def _run():
        tm = ToolsManager(runtime_state=None)
        tm.register_tool(name="a1", role="r", description="d",
                         parameters_schema={}, kind="action", effects="uncertain")
        tm.register_tool(name="a2", role="r", description="d",
                         parameters_schema={}, kind="action", effects="deterministic")
        tm.register_tool(name="p1", role="r", description="d",
                         parameters_schema={}, kind="perception")
        assert tm.uncertain_tool_names() == {"a1"}
        assert "a1" in tm.known_tool_names()

    asyncio.run(_run())


def test_grappes_detectent_la_boucle_paint():
    from core.alignment import redelegation_clusters
    paint = [
        "Ouvrir Paint et dessiner une forme",
        "Sélectionner l'outil rectangle dans Paint et tracer la forme sur le canevas",
        "Sélectionner l'outil rectangle dans Paint et tracer la forme sur le canevas de la case C4",
        "Sélectionner l'outil rectangle (G2) dans Paint et tracer depuis la case C4 vers E5",
        "Ouvrir le navigateur et chercher la météo",
    ]
    clusters = redelegation_clusters(paint)
    assert len(clusters) == 1 and len(clusters[0]) == 3


def test_grappes_vides_si_disjoints():
    from core.alignment import redelegation_clusters
    assert redelegation_clusters([
        "Ouvrir Paint via le menu Démarrer",
        "Taper du texte dans l'éditeur",
        "Fermer sans enregistrer",
    ]) == []
    assert redelegation_clusters([]) == []


def test_interdiction_intitulee_harnais():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "fr"):
        text = open(os.path.join(base, "prompts", lang, "planner.md"), encoding="utf-8").read()
        assert "INTERDICTION ÉCRITE PAR LE HARNAIS" in text
    en = open(os.path.join(base, "prompts", "en", "planner.md"), encoding="utf-8").read()
    assert "PROHIBITION WRITTEN BY THE HARNESS" in en


def test_tags_hote_brain_rendus():
    from core.prompt_loader import get_prompt_loader
    tools = [
        {"name": "hext", "kind": "action", "description": "d",
         "parameters": {"type": "object", "properties": {}}, "source": "external"},
        {"name": "hint", "kind": "action", "description": "d",
         "parameters": {"type": "object", "properties": {}}},
    ]
    out = get_prompt_loader().load(
        "planner.md", lang="base", goal="g", strategy="s", context="c",
        advice="", variable_registry={}, tools=tools, skills="",
        model_id="m", supported_modalities=[], unsupported_modalities=[])
    assert "[hôte]" in out and "[brain]" in out

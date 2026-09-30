"""Garde parametres skills : avertir toujours, refuser net a l'execution."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_skill_missing_params
from core.skills.models import SkillManifest, SkillState, ExecutionEnvironment, Checkpoint
from core.skills.registry import SkillRegistry


def _exec_step(sid, skill_id, params):
    import json as _json
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name="execute_skill",
        tool_args_json=_json.dumps({"skill_id": skill_id, "parameters": params}),
        expected_result="true",
    )


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def test_missing_param_detected():
    plan = _plan(_exec_step("s1", "skill.a.b", {"autre": 1}))
    out = find_skill_missing_params(plan, {"skill.a.b": ["fichier", "mode"]})
    assert out == {"s1": ["fichier", "mode"]}


def test_filled_params_silent():
    plan = _plan(_exec_step("s1", "skill.a.b", {"fichier": "x", "mode": "y"}))
    assert find_skill_missing_params(plan, {"skill.a.b": ["fichier", "mode"]}) == {}


def test_placeholder_counts_as_missing():
    plan = _plan(_exec_step("s1", "skill.a.b", {"fichier": "@$_param_fichier"}))
    out = find_skill_missing_params(plan, {"skill.a.b": ["fichier"]})
    assert out == {"s1": ["fichier"]}


def test_no_referential_no_warning():
    plan = _plan(_exec_step("s1", "skill.a.b", {}))
    assert find_skill_missing_params(plan, None) == {}
    assert find_skill_missing_params(plan, {}) == {}


def test_non_skill_steps_ignored():
    plan = _plan(PlanStep(id="s1", description="d", type=StepType.DIRECT_ANSWER,
                          expected_result="any", response_text="ok"))
    assert find_skill_missing_params(plan, {"skill.a.b": ["fichier"]}) == {}


def test_prompt_checklist_mentions_skill_params():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "en", "fr"):
        text = open(os.path.join(base, "prompts", lang, "plan_grammar.md"), encoding="utf-8").read()
        assert "execute_skill" in text and "@$_param_" in text, lang


def _stub_runtime(registry):
    class FakeCtx:
        def get(self, k, d=None):
            return {"mission_id": "m1"}.get(k, d)
    return types.SimpleNamespace(skill_registry=registry, execution_context=FakeCtx(),
                                 host_manifest=None, tools_manager=None,
                                 host_skill_executor=None, propagate_event=None)


def _prod_skill_with_required(tmp_path):
    reg = SkillRegistry(db_path=str(tmp_path / "skills_params_test.db"))
    man = SkillManifest(
        skill_id="skill.doc.ouvrir", namespace="t", name="Ouvrir doc",
        description="Ouvre.", parameters_schema={
            "type": "object",
            "properties": {"fichier": {"type": "string"}},
            "required": ["fichier"]},
        signature_hashes=[], environment=ExecutionEnvironment(),
        checkpoints=[Checkpoint(checkpoint_id="c", name="C")],
    )
    reg.register_draft_skill(man, flow_payload_ref="p", payload_content="{}",
                             initial_state=SkillState.PRODUCTION)
    return reg


def test_execution_refuses_missing_param_loudly(tmp_path):
    from tools.internal_tools import execute_skill_tool
    reg = _prod_skill_with_required(tmp_path)
    res = asyncio.run(execute_skill_tool(
        {"skill_id": "skill.doc.ouvrir", "parameters": {}}, _stub_runtime(reg)))
    assert res["result"] is False
    assert "fichier" in res["error_reason"]


def test_execution_accepts_filled_param(tmp_path):
    from tools.internal_tools import execute_skill_tool

    async def fake_host(ref, params):
        return {"success": True, "output": {"ok": True},
                "passed_checkpoints": ["c"], "executed_steps": []}

    reg = _prod_skill_with_required(tmp_path)
    rs = _stub_runtime(reg)
    rs.host_skill_executor = fake_host
    res = asyncio.run(execute_skill_tool(
        {"skill_id": "skill.doc.ouvrir", "parameters": {"fichier": "a.txt"}}, rs))
    assert res["result"] is True


def test_durations_accumulate_and_average(tmp_path):
    reg = SkillRegistry(db_path=str(tmp_path / "skills_dur_test.db"))
    man = SkillManifest(
        skill_id="skill.a.b", namespace="t", name="AB", description="d",
        parameters_schema={}, signature_hashes=[], environment=ExecutionEnvironment(),
        checkpoints=[],
    )
    reg.register_draft_skill(man, flow_payload_ref="p", payload_content="{}",
                             initial_state=SkillState.PRODUCTION)
    reg.transition_state("skill.a.b", 1, SkillState.PRODUCTION, reason="t")
    reg.record_run_metric("skill.a.b", 1, success=True, duration_ms=100.0)
    reg.record_run_metric("skill.a.b", 1, success=True, duration_ms=300.0)
    rows = reg.list_all_skills()
    row = next(r for r in rows if r["skill_id"] == "skill.a.b")
    assert row["timed_runs"] == 2
    assert row["avg_duration_ms"] == 200.0

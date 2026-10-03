"""Socle GUI sur : gate null, backoff pool, perceive_action, statuts separes."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_unresolved_plan_refs
from providers.base_provider import BaseProvider


def _tool_step(sid, tool, args):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name=tool, tool_args_json=args, expected_result="true",
    )


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def test_null_literal_refused_with_path():
    plan = _plan(_tool_step("step_1", "press_key", '{"key": "cliquer", "target": "null"}'))
    hits = find_unresolved_plan_refs(plan)
    assert len(hits) == 1 and "step_1" in hits[0] and "target" in hits[0]


def test_null_case_insensitive_and_nested():
    plan = _plan(_tool_step("s1", "x", '{"a": {"b": "NULL"}}'))
    assert find_unresolved_plan_refs(plan) != []


def test_empty_string_allowed():
    plan = _plan(_tool_step("s1", "type_text", '{"text": ""}'))
    assert find_unresolved_plan_refs(plan) == []


def test_wildcard_ref_refused():
    plan = _plan(_tool_step("s1", "x", '{"ref": "$@_data_*"}'))
    assert find_unresolved_plan_refs(plan) != []


class _ConcreteProvider(BaseProvider):
    async def initialize(self):
        pass

    async def generate_response(self, user_message: str) -> str:
        return ""

    async def stream_response(self, message: str, context=None, tools=None):
        return
        yield

    async def is_available(self) -> bool:
        return True

    async def generate_structured_output(self, prompt, response_schema, context=None,
                                         media_assets=None, max_output_tokens=None):
        return response_schema()


def _provider():
    p = _ConcreteProvider.__new__(_ConcreteProvider)
    p.provider_id = "test"
    p.provider_name = "test"
    p.system_prompt = ""
    p.api_keys_pool = [{"key": "k1", "is_active": True}]
    p.active_key_index = 0
    p._key_cooldowns = {}
    p._rate_limit_streak = 0
    p._pool_cooldown_until = 0.0
    return p


def test_backoff_grows_then_resets():
    p = _provider()
    w1 = p.note_rate_limit("k1")
    w2 = p.note_rate_limit("k1")
    assert 0.3 <= w1 <= 0.8
    assert w2 > w1
    assert p.has_available_keys() is False  # pool partagé en cooldown
    p.note_provider_success()
    assert p._rate_limit_streak == 0
    # Le succès efface la rafale ; la clé reste en cooldown individuel (honnête).
    assert p._pool_cooldown_until == 0.0


def test_retry_after_honored():
    p = _provider()
    assert p.note_rate_limit("k1", retry_after=5) >= 5.0


def _stub_runtime(execute_tool=None, llm=None):
    async def _fake_gen(*a, **k):
        raise AssertionError("LLM should not be called here")

    class FakeMgr:
        async def execute_tool(self, name, args):
            return await execute_tool(name, args)

    async def _llm():
        return llm

    async def fake_get_llm(rs):
        return llm

    import tools.internal_tools as it
    return types.SimpleNamespace(tools_manager=FakeMgr()), it


def test_perceive_action_rejects_missing_params():
    from tools.internal_tools import perceive_action
    rs, _ = _stub_runtime()
    out = asyncio.run(perceive_action({}, rs))
    assert out["result"] is False


def test_perceive_action_rejects_internal_tools():
    from tools.internal_tools import perceive_action
    rs, _ = _stub_runtime()
    out = asyncio.run(perceive_action(
        {"question": "q", "source_tool": "llm_analyze_data", "action_tool": "click_at"}, rs))
    assert out["result"] is False


def test_perceive_action_never_clicks_blind():
    from tools.internal_tools import perceive_action

    calls = []

    async def fake_exec(name, args):
        calls.append(name)
        return '{"result": true, "data": {"elements": []}}'

    class FakeGround:
        target_ref = ""
        reason = "rien de sur"

    class FakeLlm:
        async def generate_structured(self, **kwargs):
            return FakeGround()

    import tools.internal_tools as it
    orig = it._get_tools_llm

    async def fake_get_llm(rs):
        return FakeLlm()

    it._get_tools_llm = fake_get_llm
    try:
        rs, _ = _stub_runtime(fake_exec)
        out = asyncio.run(perceive_action(
            {"question": "ou est Edge ?", "source_tool": "perceive",
             "action_tool": "click_at", "action_args": {"cell": "$TARGET"}}, rs))
    finally:
        it._get_tools_llm = orig
    assert out["result"] is False
    assert calls == ["perceive"]  # perception oui, action JAMAIS


def test_perceive_action_happy_path_uses_fresh_ref():
    from tools.internal_tools import perceive_action

    calls = []

    async def fake_exec(name, args):
        calls.append((name, dict(args)))
        if name == "perceive":
            return '{"result": true, "data": {"elements": [{"id": "e_2_9", "name": "Edge"}]}}'
        return '{"result": true, "data": {"clicked": true}}'

    class FakeGround:
        target_ref = "e_2_9"
        reason = "unique correspondant"

    class FakeLlm:
        async def generate_structured(self, **kwargs):
            return FakeGround()

    import tools.internal_tools as it
    orig = it._get_tools_llm

    async def fake_get_llm(rs):
        return FakeLlm()

    it._get_tools_llm = fake_get_llm
    try:
        rs, _ = _stub_runtime(fake_exec)
        out = asyncio.run(perceive_action(
            {"question": "ou est Edge ?", "source_tool": "perceive",
             "action_tool": "click_at", "action_args": {"cell": "$TARGET"}}, rs))
    finally:
        it._get_tools_llm = orig
    assert out["result"] is True
    assert calls[0][0] == "perceive"
    assert calls[1] == ("click_at", {"cell": "e_2_9"})
    assert out["data"]["target"] == "e_2_9"


def test_condition_syntax_error_raises_not_skips():
    from core.executor import Executor, _ConditionError
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(variable_registry={})
    try:
        ex._evaluate_condition('"a" == ')
        raised = False
    except _ConditionError:
        raised = True
    assert raised is True
    assert ex._evaluate_condition('"Yes" == "yes"') is True


def test_skipped_crucial_field_exists():
    from core.plan_models import SolverResult, ExecutionStatus
    r = SolverResult(status=ExecutionStatus.SUCCESS, final_context="x")
    assert r.skipped_crucial_ids == []


def test_quota_streak_breaks_spiral():
    from core.plan_models import is_quota_streak
    assert is_quota_streak([]) is False
    assert is_quota_streak([("exec # :: quota epuise", 0)]) is False
    assert is_quota_streak([("exec # :: quota epuise", 0), ("exec # :: quota epuise", 0)]) is True
    assert is_quota_streak([("exec # :: quota epuise", 0), ("exec # :: timeout", 0)]) is False
    assert is_quota_streak([("exec # :: separate issue", 0)] * 2) is False


def test_solver_checks_quota_circuit():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(base, "core", "solver.py"), encoding="utf-8").read()
    assert "is_quota_streak" in text
    assert "quota_circuit_open" in text

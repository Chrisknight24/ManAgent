"""Fix 1+2 (trio faux-succès) : suspect final, strict sans monde, preuves étiquetées."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, is_suspect_final_answer
from core.solver import Solver


def _step(tool=True):
    if tool:
        return PlanStep(id="step_1", description="d", type=StepType.TOOL_CALL,
                        tool_name="keyboard", tool_args_json="{}",
                        expected_result="true")
    return PlanStep(id="step_9", description="r", type=StepType.DIRECT_ANSWER,
                    expected_result="ok", response_text="fini")


def test_suspect_seulement_sans_outil_apres_echecs():
    assert is_suspect_final_answer(_plan_tool(), [("x", 0)]) is False
    assert is_suspect_final_answer(_plan_notool(), []) is False
    assert is_suspect_final_answer(_plan_notool(), [("x", 0)]) is True


def _plan_tool():
    return Plan(goal="g", steps=[_step(True)])


def _plan_notool():
    return Plan(goal="g", steps=[_step(False)])


def _solver(calls, rum=None):
    async def _boom(*a, **k):
        calls["llm"] += 1
        raise AssertionError("LLM ne doit pas être appelé")

    class FakeTM:
        def known_tool_names(self):
            return set()

        def uncertain_tool_names(self):
            return set()

        def perception_tool_names(self):
            return set()

    solver = Solver.__new__(Solver)
    solver.id = "s1"
    solver.goal = "fermer sans enregistrer"
    solver.llm = types.SimpleNamespace(generate_structured=_boom)
    solver.current_attempt = types.SimpleNamespace(nodes=[])
    solver.runtime_state = types.SimpleNamespace(
        language="en", mission_rum=dict(rum or {}),
        tools_manager=FakeTM(),
        execution_context=types.SimpleNamespace(get=lambda k: "m1"),
    )
    return solver


def test_strict_sans_monde_refuse_sans_llm():
    calls = {"llm": 0}
    solver = _solver(calls)
    res = types.SimpleNamespace(response="tout est fermé")
    ok, why = asyncio.run(solver._verify_mission_convergence(res, strict=True))
    assert ok is False and calls["llm"] == 0
    assert "STRICTE" in why or "STRICT" in why or "monde" in why.lower()


def test_non_strict_sans_monde_passe_au_juge():
    calls = {"llm": 0}

    async def _ok(prompt, schema, tag=None, mission_id=None, **kw):
        calls["llm"] += 1
        calls["prompt"] = prompt
        from core.plan_models import ConvergenceDecision
        return ConvergenceDecision(is_convergent=True, reason="ok")

    solver = _solver(calls)
    solver.llm = types.SimpleNamespace(generate_structured=_ok)
    res = types.SimpleNamespace(response="tout est fermé")
    ok, _ = asyncio.run(solver._verify_mission_convergence(res, strict=False))
    assert ok is True and calls["llm"] == 1
    assert "[RÉPONSE]" in calls["prompt"]
    assert "UNKNOWN" in calls["prompt"]


def test_preuves_etiquetees_rum():
    calls = {"llm": 0}

    async def _ok(prompt, schema, tag=None, mission_id=None, **kw):
        calls["prompt"] = prompt
        from core.plan_models import ConvergenceDecision
        return ConvergenceDecision(is_convergent=True, reason="ok")

    solver = _solver(calls, rum={"data_x": {"value": "46", "attempt_no": 2}})
    solver.llm = types.SimpleNamespace(generate_structured=_ok)
    asyncio.run(solver._verify_mission_convergence(
        types.SimpleNamespace(response="46"), strict=False))
    assert "[RUM data_x t2]" in calls["prompt"]
    assert "46" in calls["prompt"]


def test_stats_snapshot_emis_fin_mission(monkeypatch):
    from utils.logger import Logger
    events = []
    monkeypatch.setattr(Logger, "event",
                        staticmethod(lambda event_type, **fields: events.append(
                            {"event": event_type, **fields})))
    solver = Solver.__new__(Solver)
    solver.id = "m-stats"
    solver.runtime_state = types.SimpleNamespace(
        llm_usage={"total": {"total_tokens": 10, "calls": 2}, "by_mission": {}},
        truncation_count={"Plan": 1},
    )

    async def _fake_propagate(name, payload):
        events.append({"event": name, "packet": True, **payload})

    solver.propagate_event = _fake_propagate
    asyncio.run(solver._emit_stats_snapshot())
    snaps = [e for e in events if e["event"] == "stats.snapshot"]
    assert len(snaps) == 2
    assert sum(1 for e in snaps if not e.get("packet")) == 1
    pkt = next(e for e in snaps if e.get("packet"))
    assert pkt["mission_id"] == "m-stats"
    assert pkt["total"]["total_tokens"] == 10
    assert pkt["truncations"] == {"Plan": 1}


def test_tag_attempt_marque_tentative():
    from core.executor import Executor
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(
        current_attempt=types.SimpleNamespace(attempt_number=3))
    entry = ex._tag_attempt({"value": "x"})
    assert entry["attempt_no"] == 3


def test_grosse_donnee_outil_encapsulee_sans_crash():
    import json as _json
    from core.executor import Executor
    from core.plan_models import PlanStep, StepType

    async def _fake_exec(name, args):
        return _json.dumps({"result": True, "data": "x" * 5000})

    solver = types.SimpleNamespace(
        id="s-big",
        variable_registry={},
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=types.SimpleNamespace(
            session_id="sess", discovery_engine=None,
            tools_manager=types.SimpleNamespace()),
        execute_tool=_fake_exec,
    )
    ex = Executor.__new__(Executor)
    ex.solver = solver
    step = PlanStep(id="step_1", description="gros", type=StepType.TOOL_CALL,
                    tool_name="keyboard", tool_args_json="{}",
                    expected_result="true")
    ok, out, _ = asyncio.run(ex._handle_tool_call(
        step, node=types.SimpleNamespace()))
    assert ok is True
    entry = solver.variable_registry.get("data_step_1")
    assert entry is not None and entry.get("type") == "asset"
    assert entry.get("attempt_no") == 1


def test_convergence_check_relaye_paquet(monkeypatch):
    from utils.logger import Logger
    from core.executor import Executor
    logs = []
    packets = []
    monkeypatch.setattr(Logger, "event",
                        staticmethod(lambda event_type, **fields: logs.append(event_type)))

    async def _fake_propagate(name, payload):
        packets.append((name, payload))

    solver = types.SimpleNamespace(
        current_attempt=types.SimpleNamespace(attempt_number=1),
        runtime_state=types.SimpleNamespace(
            execution_context=types.SimpleNamespace(get=lambda k: "m-pkt")),
        propagate_event=_fake_propagate,
    )
    ex = Executor.__new__(Executor)
    ex.solver = solver
    asyncio.run(ex._emit_convergence_check("fail", "r", step_id="step_9"))
    assert logs.count("convergence.check") == 1
    assert packets == [("convergence.check", {
        "verdict": "fail", "detail": "r", "full": "r",
        "mission_id": "m-pkt", "step_id": "step_9"})]

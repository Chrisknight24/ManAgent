"""BUG E' (P2, ciblé) — le refus de convergence finale doit être ACTIONNABLE.

Au retry, le planner recevait « but non atteint » sans cible : il rejouait la
même approche. On ajoute, côté Solver, la dernière étape RÉELLE probablement en
cause + la consigne de changer l'approche de CETTE étape (pas juste l'attente).
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.alignment as alignment  # noqa: E402
import core.solver as solver_mod  # noqa: E402
from core.plan_models import ConvergenceDecision  # noqa: E402
from core.solver import Solver, _last_real_node  # noqa: E402


def _node(desc, exp, stype="tool_call", status="success", sid="step_1"):
    return types.SimpleNamespace(
        step_id=sid, description=desc, expected_result=exp,
        step_type=stype, status=status)


def test_last_real_node_ignore_direct_answer():
    nodes = [
        _node("action", "x", sid="step_1"),
        _node("repond", "y", stype="direct_answer", sid="step_2"),
    ]
    assert _last_real_node(nodes).step_id == "step_1"


def test_last_real_node_aucun():
    assert _last_real_node([]) is None
    assert _last_real_node([_node("echec", "x", status="failed")]) is None


class _Capture:
    def load(self, name, **kwargs):
        return "PROMPT"


class _RefuseLlm:
    def clone(self, role_name=None):
        return self

    def enable_discovery(self, *a, **k):
        pass

    def set_data_context(self, *a, **k):
        pass

    async def generate_structured(self, **k):
        return ConvergenceDecision(is_convergent=False, reason="but non atteint")


def _make_solver(monkeypatch, nodes):
    monkeypatch.setattr(solver_mod, "get_prompt_loader", lambda: _Capture())

    async def _fake_snap(rs, max_chars=800):
        return "MONDE"

    monkeypatch.setattr(alignment, "fetch_world_state_snapshot", _fake_snap)

    s = Solver.__new__(Solver)
    s.goal = "but"
    s.id = "m"
    s.depth = 0
    s.variable_registry = {}
    s.executor = types.SimpleNamespace(_perception_trail=[])
    s.runtime_state = types.SimpleNamespace(
        language="en", mission_rum={}, perception_warnings_by_solver={})
    s.current_attempt = types.SimpleNamespace(nodes=nodes)
    s.llm = _RefuseLlm()
    return s


def test_feedback_cible_nomme_etape(monkeypatch):
    s = _make_solver(monkeypatch, [_node("clique bouton lecture", "attente reprise", sid="step_3")])
    ok, why = asyncio.run(
        s._verify_mission_convergence(types.SimpleNamespace(response="fait"), strict=False))
    assert ok is False
    assert "step_3" in why
    assert "clique bouton lecture" in why
    assert "Change l'approche" in why


def test_feedback_sans_noeud_reel_reste_propre(monkeypatch):
    s = _make_solver(monkeypatch, [_node("repond", "x", stype="direct_answer", sid="step_9")])
    ok, why = asyncio.run(
        s._verify_mission_convergence(types.SimpleNamespace(response="fait"), strict=False))
    assert ok is False
    assert why == "but non atteint"

"""Porte deterministe de convergence sur les lacunes de plan (`plan_gaps`).

Bug reel (mission `ee848a24`, 02:32:05) : le plan n'ouvrait que Paint, deux
lacunes etaient acceptees (« outil de dessin non selectionne », « carre non
dessine »), aucune perception n'a ete executee, et le juge LLM a repondu
`is_convergent=true, reason="Mission accomplie."` (`prompts_log/0009`).

Regle : des lacunes non vides **et** aucune preuve de perception non vide
depuis l'acceptation du plan = refus, **sans appeler le LLM**. Si l'hote ne
fournit aucun outil de perception, la porte est desactivee (elle serait
impossible a satisfaire) et un avertissement est journalise.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver, gap_convergence_refusal

_GAP = "le carre complet n'est pas encore dessine"


def test_no_gaps_never_blocks():
    assert gap_convergence_refusal([], [], True) is None


def test_gap_with_perception_proof_is_left_to_the_judge():
    proof = "[PERCEPTION data_step_6] un carre noir est visible au centre du canevas"
    assert gap_convergence_refusal([_GAP], [proof], True) is None


def test_gap_without_any_perception_proof_is_refused():
    why = gap_convergence_refusal([_GAP], [], True)
    assert isinstance(why, str) and why
    assert "lacune" in why.lower() or "gap" in why.lower()


def test_gate_is_disabled_when_no_perception_tool_exists():
    assert gap_convergence_refusal([_GAP], [], False) is None


def _solver(gaps, trail=(), registry=None, perception_tools=True):
    calls = {"n": 0}

    async def fake_gen(prompt, schema, tag=None, **kw):
        calls["n"] += 1
        return SimpleNamespace(is_convergent=True, reason="ok")

    solver = Solver.__new__(Solver)
    solver.id = "s-gap"
    solver.goal = "ouvrir Paint puis dessiner un carre"
    solver.llm = SimpleNamespace(generate_structured=fake_gen)
    solver.runtime_state = SimpleNamespace(
        language="fr",
        mission_rum={},
        perception_warnings_by_solver={},
        plan_warnings_by_solver={"s-gap": list(gaps)} if gaps is not None else {},
        tools_manager=SimpleNamespace(
            perception_tool_names=lambda: ({"perceive_understand"} if perception_tools else set()),
            uncertain_tool_names=lambda: set(),
        ),
    )
    solver.variable_registry = dict(registry or {})
    solver.executor = SimpleNamespace(_perception_trail=list(trail))
    solver.current_attempt = SimpleNamespace(nodes=[])
    return solver, calls


def _result():
    return SimpleNamespace(response="Paint est ouvert.", final_context="Paint est ouvert.")


def test_mission_1_replay_is_refused_without_llm():
    solver, calls = _solver(
        ["l'outil de dessin n'est pas selectionne", _GAP],
        trail=[],
        registry={},
    )
    ok, why = asyncio.run(solver._verify_mission_convergence(_result()))
    assert ok is False
    assert why
    assert calls["n"] == 0  # aucun appel LLM : la porte tranche seule
    assert getattr(solver.runtime_state, "last_convergence_ok", None) is False


def test_gap_with_real_perception_evidence_reaches_the_judge():
    solver, calls = _solver(
        [_GAP],
        trail=["data_step_6"],
        registry={"data_step_6": {"value": "un carre noir est visible au centre"}},
    )
    ok, _why = asyncio.run(solver._verify_mission_convergence(_result()))
    assert ok is True
    assert calls["n"] == 1


def test_gap_with_empty_perception_value_is_still_refused():
    solver, calls = _solver(
        [_GAP],
        trail=["data_step_6"],
        registry={"data_step_6": {"value": "   "}},
    )
    ok, _why = asyncio.run(solver._verify_mission_convergence(_result()))
    assert ok is False
    assert calls["n"] == 0


def test_no_gap_keeps_historical_behaviour():
    solver, calls = _solver(None, trail=[], registry={})
    ok, _why = asyncio.run(solver._verify_mission_convergence(_result()))
    assert ok is True
    assert calls["n"] == 1


def test_gate_disabled_without_perception_tool_keeps_llm():
    solver, calls = _solver([_GAP], trail=[], registry={}, perception_tools=False)
    ok, _why = asyncio.run(solver._verify_mission_convergence(_result()))
    assert ok is True
    assert calls["n"] == 1

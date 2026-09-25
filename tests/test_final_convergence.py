"""Tests vérification finale root + court-circuit PD vide (rapides, LLM simulés)."""
import sys
import os
import asyncio
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import Llm
from core.plan_models import ConvergenceDecision


def _llm():
    return Llm(provider_manager=SimpleNamespace(), provider_id="x",
               model_id="y", runtime_state=SimpleNamespace(language="en"))


def test_pd_empty_goes_legacy_without_llm_call(monkeypatch):
    llm = _llm()
    calls = {"legacy": 0, "direct": 0}

    async def fake_legacy(self, prompt, schema, tag=None, mission_id=None, media_assets=None):
        calls["legacy"] += 1
        return "LEGACY-OK"

    async def fake_direct(*a, **k):
        calls["direct"] += 1
        raise AssertionError("aucun appel LLM ne devrait partir sans matière PD")

    monkeypatch.setattr(Llm, "_build_discovery_section", lambda self, s, b=None: "")
    monkeypatch.setattr(Llm, "_generate_structured_legacy", fake_legacy)
    monkeypatch.setattr(Llm, "_call_llm_with_schema", fake_direct)
    out = asyncio.run(llm.generate_structured(
        prompt="p", schema=ConvergenceDecision, tag="t"))
    assert out == "LEGACY-OK" and calls == {"legacy": 1, "direct": 0}


def _solver(decision=None, exc=None):
    from core.solver import Solver
    s = Solver.__new__(Solver)
    s.id = "test-solver"
    s.goal = "ouvrir X"

    async def fake_gen(prompt, schema, tag=None, **kw):
        if exc:
            raise exc
        return SimpleNamespace(is_convergent=decision, reason="pourquoi")

    s.llm = SimpleNamespace(generate_structured=fake_gen)
    s.runtime_state = SimpleNamespace(language="en")
    return s


def test_final_convergence_ok():
    from core.solver import Solver  # noqa: F401 (import lourd mais partagé)
    s = _solver(decision=True)
    res = SimpleNamespace(final_context="tout a marché")
    assert asyncio.run(s._verify_mission_convergence(res)) == (True, "")


def test_final_convergence_ko_blocks():
    s = _solver(decision=False)
    res = SimpleNamespace(final_context="rien ne marche")
    ok, why = asyncio.run(s._verify_mission_convergence(res))
    assert ok is False and why == "pourquoi"


def test_final_convergence_infra_fail_open():
    s = _solver(exc=RuntimeError("panne LLM"))
    res = SimpleNamespace(final_context="...")
    ok, _ = asyncio.run(s._verify_mission_convergence(res))
    assert ok is True

"""Vague convergence+juge Bloc A : prefixe depouille, statut structure, court-circuit FAILED."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.plan_models import ConvergenceDecision, PlanStep, StepType


def _abs_step():
    return PlanStep(
        id="step_9", description="sous-tache abstraite", type=StepType.ABSTRACT_TASK,
        expected_result="travail fait",
    )


def _solver(calls, discovery_enabled=True):
    async def fake_gen(prompt, schema, tag=None, mission_id=None, with_discovery=None, **kw):
        calls["prompts"].append(prompt)
        calls["tag"] = tag
        return ConvergenceDecision(is_convergent=True, reason="ok")

    llm = SimpleNamespace(
        _discovery_enabled=discovery_enabled,
        enable_discovery=lambda eng, ent: None,
        disable_discovery=lambda: None,
        set_data_context=lambda ctx: None,
        generate_structured=fake_gen,
    )
    try:
        import core.executor as _exmod
        _orig_clone = getattr(llm, "clone", None)
    except Exception:
        pass
    llm.clone = lambda role_name=None: llm
    rs = SimpleNamespace(
        language="en",
        discovery_engine=SimpleNamespace(),
        execution_context=SimpleNamespace(get=lambda k: "m1"),
    )
    solver = SimpleNamespace(
        llm=llm, runtime_state=rs, variable_registry={},
        parent=None, _data_providers={},
    )
    return solver


def _executor(solver):
    ex = Executor.__new__(Executor)
    ex.solver = solver
    return ex


def test_strip_tool_prefix_pur():
    assert Executor._strip_tool_prefix("[TOOLS OK] resultat 46") == "resultat 46"
    assert Executor._strip_tool_prefix("[TOOLS FAILED] boom") == "boom"
    assert Executor._strip_tool_prefix("texte [TOOLS OK] au milieu") == "texte [TOOLS OK] au milieu"
    assert Executor._strip_tool_prefix("fini") == "fini"


def test_prompt_juge_depoille_statut_unique():
    calls = {"prompts": []}
    ex = _executor(_solver(calls))
    out = asyncio.run(ex._evaluate_semantic_convergence(_abs_step(), "[TOOLS OK] resultat 46"))
    assert out.is_convergent is True
    prompt = calls["prompts"][0]
    assert "[TOOLS OK]" not in prompt
    assert "[TOOLS FAILED]" not in prompt
    assert "resultat 46" in prompt


def test_statut_structure_ok_sans_parsing():
    calls = {"prompts": []}
    ex = _executor(_solver(calls))
    out = asyncio.run(ex._evaluate_semantic_convergence(_abs_step(), "texte brut sans prefixe", tool_status="OK"))
    assert out.is_convergent is True
    assert "texte brut sans prefixe" in calls["prompts"][0]


def test_court_circuit_failed_sans_llm():
    calls = {"prompts": []}
    ex = _executor(_solver(calls))
    out = asyncio.run(ex._check_convergence(_abs_step(), "[TOOLS FAILED] echec net", action_success=False))
    assert out.is_convergent is False
    assert calls["prompts"] == []


def test_succes_structure_passe_au_juge():
    calls = {"prompts": []}
    ex = _executor(_solver(calls))
    out = asyncio.run(ex._check_convergence(_abs_step(), "[TOOLS OK] fini", action_success=True))
    assert out.is_convergent is True
    assert len(calls["prompts"]) == 1
    assert "[TOOLS OK]" not in calls["prompts"][0]

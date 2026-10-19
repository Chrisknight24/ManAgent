"""BUG F bloc 6 — trace d'exécution compactée (dédoublonnée).

Sur échec, `final_context` incluait `accumulated_context` (input + lignes
`[Succès id] : output`) ET `failure_trace` (qui re-liste DÉJÀ les étapes
réussies). Les sorties réussies apparaissaient donc DEUX fois. On garde
l'input + la failure_trace : plus de doublon, aucune perte (la trace contient
déjà tout le détail).
"""
import asyncio
import os
import sys
import types
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor
from core.plan_models import ConvergenceDecision, Plan, PlanStep, StepType


class _Ctx:
    def scope(self, **k):
        @contextmanager
        def _cm():
            yield
        return _cm()

    def get(self, k, d=None):
        return "m1"


def _solver():
    async def _noop(*a, **k):
        return None

    rs = types.SimpleNamespace(
        cancel_requested=False, mission_rum={}, execution_context=_Ctx(),
        language="en")
    return types.SimpleNamespace(
        id="m", variable_registry={}, runtime_state=rs,
        _get_registry_metadata_view=lambda: {}, propagate_event=_noop)


def _executor(solver, outputs, converges):
    ex = Executor.__new__(Executor)
    ex.solver = solver
    ex._applied_waits = []
    ex._perception_trail = []
    ex._step_event_meta = lambda node: {}
    ex._propagate_crucial_variable = lambda name: None

    async def _credit(child, ok):
        return None

    async def _wait(step, when):
        return 0

    async def _exec_step(step, ctx, node=None):
        return outputs[step.id]

    async def _conv(step, out, sup, node=None, action_success=True):
        return ConvergenceDecision(is_convergent=converges[step.id], reason="x")

    ex._credit_abstract_child = _credit
    ex._apply_step_wait = _wait
    ex._execute_step_action = _exec_step
    ex._check_convergence = _conv
    return ex


def _plan():
    return Plan(goal="g", steps=[
        PlanStep(id="step_1", description="ok", type=StepType.TOOL_CALL,
                 tool_name="t", tool_args_json="{}", expected_result="e"),
        PlanStep(id="step_2", description="ko", type=StepType.TOOL_CALL,
                 tool_name="t", tool_args_json="{}", expected_result="e"),
    ])


def test_pas_de_doublon_des_sorties_reussies_sur_echec():
    solver = _solver()
    ex = _executor(
        solver,
        outputs={"step_1": (True, "RESULT_X", None),
                 "step_2": (False, "", "invalid args")},
        converges={"step_1": True, "step_2": False})
    attempt = types.SimpleNamespace(add_node=lambda n: None)

    res = asyncio.run(ex.execute_plan(_plan(), "INPUT-CTX", attempt))

    assert res.status.name == "FAILED" or str(res.status).endswith("FAILED")
    ctx = res.final_context
    assert "INPUT-CTX" in ctx             # input conservé
    assert "RESULT_X" in ctx             # la sortie réussie est là (via trace)
    assert ctx.count("RESULT_X") == 1    # ... une seule fois

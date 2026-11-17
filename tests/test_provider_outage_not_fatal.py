"""Une panne fournisseur ne tue plus la mission sur-le-champ.

Vecu (run du 16/11, deux fois de suite) : Google a repondu
`503 UNAVAILABLE - This model is currently experiencing high demand` au moment
du Plan. L'exception traversait la boucle du solver et tombait dans le
`except Exception` final : mission morte immediatement, sans consommer les
tentatives, sans message honnete, et sans avoir essaye les huit autres cles.

Desormais l'erreur est classee `INFRA_FAILURE` : l'arbre la voit, l'historique
la compte, et les disjoncteurs existants (quota x2, infra x3) arretent
proprement si elle persiste — au lieu d'accuser le plan.

Le harnais est celui de `test_budget.py` : un vrai `Solver.run()` avec un
planner et un executor factices.
"""

import asyncio
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.execution_models import FailureClass
from core.plan_models import ExecutionStatus
from core.solver import Solver
from providers.base_provider import (
    ProviderQuotaExhaustedError,
    ProviderServiceUnavailableError,
)


def _runtime():
    return SimpleNamespace(
        cancel_requested=False,
        solver_registry={},
        cache_manager=None,
        skill_registry=None,
        discovery_engine=None,
        execution_context=SimpleNamespace(scope=lambda **kw: nullcontext()),
        update_marker=lambda k, v: None,
        generation_epoch=0,
    )


def _solver(behaviors):
    """`behaviors` : "ok", "provider" (503) ou "quota" (quota epuise)."""
    s = Solver.__new__(Solver)
    s.id = "root-test"
    s.goal = "but generique"
    s.depth = 0
    s.context = ""
    s.parent = SimpleNamespace(id="parent")
    s.parent_step_id = None
    s.runtime_state = _runtime()
    s.signatures = []
    s._similar_missions = None
    s.variable_registry = {}
    s.current_attempt = None
    s._preexecution_failures = 0
    s.last_failure_bundle = None
    s.last_breakout_report = None
    s._candidate_skills = []

    calls = {"plans": 0, "executions": 0}

    async def fake_feasibility(*a, **k):
        return SimpleNamespace(is_possible=True, reason="", refined_strategy="strat")

    async def fake_plan(*a, **k):
        calls["plans"] += 1
        action = behaviors[min(calls["plans"] - 1, len(behaviors) - 1)]
        if action == "provider":
            raise ProviderServiceUnavailableError("panne simulee du fournisseur")
        if action == "quota":
            raise ProviderQuotaExhaustedError("quota simule epuise")
        return SimpleNamespace(steps=[], model_dump=lambda mode="json": {})

    async def fake_execute(*a, **k):
        calls["executions"] += 1
        return SimpleNamespace(
            status=ExecutionStatus.SUCCESS,
            final_context="tour fini",
            error_reason=None,
            failure_class=FailureClass.NONE,
            target_entity=None,
            failure_bundle=None,
            breakout_report=None,
            material_success_count=0,
            response="ok",
            execution_tree=None,
        )

    async def fake_verify(final_result, strict=False):
        return (True, "")

    async def fake_validate(*a, **k):
        return True

    async def fake_propagate(*a, **k):
        return None

    async def fake_skill(*a, **k):
        return None

    s._check_feasibility = fake_feasibility
    s.planner = SimpleNamespace(propose_plan=fake_plan, _cached_advice=None)
    s.executor = SimpleNamespace(execute_plan=fake_execute)
    s._verify_mission_convergence = fake_verify
    s.validate_plan = fake_validate
    s.propagate_event = fake_propagate
    s._handle_skill_lifecycle_post_execution = fake_skill
    s._get_registry_metadata_view = lambda: {}
    s._format_registry_view = lambda meta: ""
    s.calls = calls
    return s


def test_un_503_au_plan_est_retente_puis_reussi():
    """Une panne passagere coute une tentative, pas la mission."""
    s = _solver(["provider", "ok"])
    res = asyncio.run(s.run())

    assert res.status == ExecutionStatus.SUCCESS
    assert s.calls["plans"] == 2
    assert s.calls["executions"] == 1


def test_trois_pannes_consecutives_arretent_proprement():
    """L'infra persiste : arret honnete apres 3, sans exception qui remonte."""
    s = _solver(["provider"])
    res = asyncio.run(s.run())

    assert res.status == ExecutionStatus.FAILED
    assert s.calls["plans"] == 3
    assert s.calls["executions"] == 0
    # Le message porte la cause reelle, pas « le plan a echoue ».
    assert "panne simulee" in (res.error_reason or "")


def test_le_quota_ouvre_son_disjoncteur_au_deuxieme():
    """Un quota vide ne se repare pas en replanifiant : 2 essais suffisent."""
    s = _solver(["quota"])
    res = asyncio.run(s.run())

    assert res.status == ExecutionStatus.FAILED
    assert s.calls["plans"] == 2
    assert s.calls["executions"] == 0

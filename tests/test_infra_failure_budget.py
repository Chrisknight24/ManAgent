"""Panne d'infrastructure : ni budget de convergence, ni accusation du plan.

Vecu (run dcc9ec4b) : deux timeouts fournisseur ont suffi a ouvrir le
disjoncteur de convergence, alors que le plan n'y etait pour rien. Un
troisieme aurait du arreter la mission avec un message honnete.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.execution_models import INFRA_FAILURE_CODES, FailureClass
from core.executor import _failure_error_code
from core.plan_models import (
    classify_failure_origin,
    is_infra_streak,
    is_sterile_streak,
    normalize_failure_signature,
)


# --------------------------------------------------------------------------
# Origine et signature
# --------------------------------------------------------------------------

def test_infra_a_sa_propre_origine():
    origin = classify_failure_origin(
        FailureClass.INFRA_FAILURE,
        "Le selecteur n'a pas pu repondre (PROVIDER_UNAVAILABLE).",
        error_code="PROVIDER_UNAVAILABLE",
    )
    assert origin == "INFRA"


def test_code_seul_suffit_a_classer_infra():
    origin = classify_failure_origin(
        FailureClass.EXECUTION_FAILURE, "appel echoue", error_code="PROVIDER_UNAVAILABLE"
    )
    assert origin == "INFRA"


def test_un_alignement_reste_un_alignement():
    origin = classify_failure_origin(FailureClass.ALIGNMENT_FAILURE, "cible absente")
    assert origin == "ALIGNMENT"


def test_infra_ne_ressemble_pas_a_un_refus_hote():
    origin = classify_failure_origin(
        FailureClass.INFRA_FAILURE, "provider injoignable", error_code="PROVIDER_QUOTA_EXHAUSTED"
    )
    assert origin == "INFRA"


# --------------------------------------------------------------------------
# Disjoncteurs
# --------------------------------------------------------------------------

def _sig(code="PROVIDER_UNAVAILABLE", cls=FailureClass.INFRA_FAILURE):
    return normalize_failure_signature(cls, "panne provider", code=code)


def test_infra_streak_se_declenche_au_troisieme():
    one = _sig()
    assert is_infra_streak([(one, 0), (one, 0)]) is False
    assert is_infra_streak([(one, 0), (one, 0), (one, 0)]) is True


def test_infra_ne_ressemble_pas_a_une_sterilite():
    """Une panne provider n'est jamais « le meme echec sans progres »."""
    history = [(_sig(), 0), (_sig(), 0)]
    assert is_sterile_streak(history) is False


def test_infra_streak_ignore_les_autres_causes():
    infra = _sig()
    autre = normalize_failure_signature(
        FailureClass.EXECUTION_FAILURE, "outil casse", code="TOOL_ERROR"
    )
    assert is_infra_streak([(infra, 0), (autre, 0), (infra, 0)]) is False


# --------------------------------------------------------------------------
# Lecture du code d'erreur reellement porte par l'executeur
# --------------------------------------------------------------------------

def _node(bundle):
    return SimpleNamespace(failure_bundle=bundle)


def test_code_lu_dans_la_forme_fermee_des_outils():
    node = _node({"error": {"code": "PROVIDER_UNAVAILABLE", "message": "down"}})
    assert _failure_error_code(node) == "PROVIDER_UNAVAILABLE"


def test_code_lu_dans_la_forme_objet():
    node = _node(SimpleNamespace(error_code="PROVIDER_QUOTA_EXHAUSTED"))
    assert _failure_error_code(node) == "PROVIDER_QUOTA_EXHAUSTED"


def test_code_absent_ou_illisible():
    assert _failure_error_code(_node(None)) == ""
    assert _failure_error_code(_node({})) == ""
    assert _failure_error_code(SimpleNamespace()) == ""


def test_codes_infra_figes():
    assert "PROVIDER_UNAVAILABLE" in INFRA_FAILURE_CODES
    assert "PROVIDER_QUOTA_EXHAUSTED" in INFRA_FAILURE_CODES
    assert "TARGET_NOT_FOUND" not in INFRA_FAILURE_CODES


# --------------------------------------------------------------------------
# Comptabilite : une panne d'infra ne consomme pas le budget
# --------------------------------------------------------------------------

def test_infra_hors_budget_dans_l_evenement(monkeypatch):
    from utils.logger import Logger
    from core.solver import Solver

    captured = {}

    def _fake_event(name, **fields):
        captured.setdefault(name, []).append(fields)

    monkeypatch.setattr(Logger, "event", staticmethod(_fake_event))

    stub = SimpleNamespace(
        id="s1",
        runtime_state=SimpleNamespace(execution_markers={}),
    )
    attempt = SimpleNamespace(
        attempt_number=2,
        plan_id="p1",
        outcome="failed",
        failure_class=FailureClass.INFRA_FAILURE,
    )
    Solver._emit_attempt_event(stub, "attempt_failed", attempt, "provider down")

    fields = captured["attempt_failed"][0]
    assert fields["counts_toward_budget"] is False
    assert fields["failure_class"] == "infra_failure"

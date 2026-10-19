"""Palier 2 — recul stratégie étendu (F3 + rejets répétés).

Le recul (re-faisabilité + stratégie) doit se déclencher :

- convergence_failure : dès le 1er échec (plan exécuté, stratégie en cause) ;
- execution_failure  : après 2 échecs de même classe (comportement historique) ;
- plan_rejected      : après 2 rejets CONSÉCUTIFS du superviseur.

La décision est isolée dans un helper pur `Solver._recul_key_for` (sans LLM).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver


def _h(*classes):
    return [(f"{c} :: detail", 0) for c in classes]


def test_aucun_echec_aucun_recul():
    assert Solver._recul_key_for([], 0) is None


def test_convergence_declenche_immediatement():
    assert Solver._recul_key_for(_h("convergence_failure"), 0) == "convergence_failure"


def test_execution_pas_de_recul_avant_2():
    assert Solver._recul_key_for(_h("execution_failure"), 0) is None


def test_execution_recul_apres_2_meme_classe():
    key = Solver._recul_key_for(_h("execution_failure", "execution_failure"), 0)
    assert key == "execution_failure"


def test_execution_classes_differentes_pas_de_recul():
    assert Solver._recul_key_for(_h("convergence_failure", "execution_failure"), 0) is None


def test_un_seul_rejet_superviseur_pas_de_recul():
    assert Solver._recul_key_for([], 1) is None


def test_deux_rejets_consecutifs_declenchent_recul():
    assert Solver._recul_key_for([], 2) == "plan_rejected"


def test_convergence_prime_sur_rejets():
    key = Solver._recul_key_for(_h("convergence_failure"), 2)
    assert key == "convergence_failure"


def test_robuste_aux_signatures_malformees():
    assert Solver._recul_key_for([("", 0), (None, 0)], 0) is None

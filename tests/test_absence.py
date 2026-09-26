"""Cas Amazon : une absence ne doit plus valider une étape (rapide, sans LLM)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor


def _ex():
    return Executor.__new__(Executor)


def test_amazon_filter_absence_rejected():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="true",
        actual="Aucun filtre ou option de prix n'est visible sur l'écran actuel, l'élément recherché n'est donc pas présent.",
        supplemental_data="Aucun filtre ou option de prix n'est visible sur l'écran actuel",
        raw_success_flag="true",
    )
    assert ok is False


def test_amazon_sunglasses_absence_rejected():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="true",
        actual="Aucune lunette de soleil ni prix n'est visible sur l'écran actuel, car aucune recherche n'a encore été effectuée sur la page.",
        supplemental_data="Analyse terminée.",
        raw_success_flag="true",
    )
    assert ok is False


def test_true_find_still_accepted():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="true",
        actual="Found 3 sunglasses under 20 dollars, IDs e_1 e_2 e_3, ready to click.",
        supplemental_data="Analyse terminée.",
        raw_success_flag="true",
    )
    assert ok is True


def test_count_zero_still_accepted():
    ex = _ex()
    ok, _why = ex._verify_rigid_outcome(
        expected="true",
        actual="Analyse effectuée : 0 erreur critique trouvée, aucune défaillance détectée.",
        supplemental_data="Analyse terminée.",
        raw_success_flag="true",
    )
    assert ok is True

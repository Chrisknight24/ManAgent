"""BUG-1 — collision de registre inter-tentatives (`$@_data_step_N`).

Le suffixe d'instance (`-00036`) est GLOBAL et n'encode pas la tentative.
Chaque tentative régénère les mêmes noms locaux (`step_5`). La résolution
floue doit privilégier la tentative courante (ou la plus récente), jamais la
première clé rencontrée (sinon une variable d'une tentative passée est lue).
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor


def _executor(registry, attempt_no=None):
    ex = Executor.__new__(Executor)
    solver = types.SimpleNamespace(variable_registry=registry)
    if attempt_no is not None:
        solver.current_attempt = types.SimpleNamespace(attempt_number=attempt_no)
    ex.solver = solver
    return ex


def test_collision_inter_tentatives_resout_tentative_courante():
    # Scénario apollo : en tentative 1, step_5 = frappe ENTRÉE (dict) ;
    # en tentative 4, step_5 = perceive_understand ("No, VLC..."). Le planner
    # référence `$@_data_step_5` : il doit lire la valeur de la tentative 4.
    reg = {
        "data_step_5-00036": {"value": {"key": "[ENTER]", "pressed": True}, "attempt_no": 1},
        "data_step_5-00099": {"value": "No, VLC n'est pas actif.", "attempt_no": 4},
    }
    ex = _executor(reg, attempt_no=4)
    key, entry = ex._find_var_in_registry("data_step_5")
    assert key == "data_step_5-00099"
    assert entry["value"] == "No, VLC n'est pas actif."


def test_plus_recent_prime_sans_attempt_no():
    # Sans métadonnée de tentative : l'entrée la plus récente (plus grand
    # suffixe global) gagne, jamais la première insérée.
    reg = {
        "data_step_5-00036": {"value": "ancien"},
        "data_step_5-00099": {"value": "recent"},
    }
    ex = _executor(reg)
    key, entry = ex._find_var_in_registry("data_step_5")
    assert key == "data_step_5-00099"
    assert entry["value"] == "recent"


def test_pas_de_confusion_bool_data():
    reg = {
        "data_step_5-00099": {"value": "yes"},
        "bool_step_5-00100": {"value": "true"},
    }
    ex = _executor(reg, attempt_no=4)
    key, entry = ex._find_var_in_registry("bool_step_5")
    assert key == "bool_step_5-00100"
    assert entry["value"] == "true"

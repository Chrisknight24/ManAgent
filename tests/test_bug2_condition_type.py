"""BUG-2 — `execute_if` comparant une variable NON scalaire (dict/liste).

Une interpolation qui produit un dict/liste est « insuffisante » par nature :
la condition doit être évaluée à False (+ avertissement), SANS lever
`_ConditionError`. Sinon une étape cruciale est tuée au lieu d'être sautée.
Les vraies erreurs de syntaxe continuent, elles, de lever `_ConditionError`.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor, _ConditionError


def _executor(value, attempt_no=4):
    ex = Executor.__new__(Executor)
    ex.solver = types.SimpleNamespace(
        variable_registry={"data_step_5-00099": {"value": value, "attempt_no": attempt_no}},
        current_attempt=types.SimpleNamespace(attempt_number=attempt_no),
    )
    return ex


def test_condition_sur_dict_ne_crash_pas():
    # Cas apollo : `$@_data_step_5` = dict clavier -> False, jamais d'exception.
    ex = _executor({"key": "[ENTER]", "pressed": True})
    assert ex._evaluate_condition('$@_data_step_5 == "yes"') is False


def test_condition_sur_liste_ne_crash_pas():
    ex = _executor(["a", "b"])
    assert ex._evaluate_condition('$@_data_step_5 == "yes"') is False


def test_condition_scalaire_yes_no_ok():
    assert _executor("yes")._evaluate_condition('$@_data_step_5 == "yes"') is True
    assert _executor("no")._evaluate_condition('$@_data_step_5 == "yes"') is False


def test_condition_malformee_leve_toujours():
    with pytest.raises(_ConditionError):
        _executor("yes")._evaluate_condition("== 5")

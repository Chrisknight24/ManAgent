"""Logs avec pile : l'appel ne casse jamais la signature. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import Logger


def test_error_with_traceback_inside_except_does_not_raise(capsys):
    not_callable = "x"
    try:
        not_callable()
    except TypeError as e:
        Logger.error(f"panne visee : {e}", exc_info=True)
    out = capsys.readouterr().err
    assert "panne visee" in out
    assert "Traceback" in out


def test_error_with_traceback_outside_except_does_not_raise(capsys):
    Logger.error("simple message", exc_info=True)
    out = capsys.readouterr().err
    assert "simple message" in out

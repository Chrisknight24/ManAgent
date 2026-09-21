"""Tests protocol_version : le garde-fou dev/prod (fonction pure, rapides)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import PROTOCOL_VERSION, check_protocol_version


def test_matching_version_accepted():
    ok, _ = check_protocol_version(PROTOCOL_VERSION)
    assert ok is True


def test_missing_version_accepted_with_warning():
    ok, msg = check_protocol_version(None)
    assert ok is True
    assert "absent" in msg


def test_different_version_refused_loudly():
    ok, msg = check_protocol_version("999")
    assert ok is False
    assert "mismatch" in msg
    assert PROTOCOL_VERSION in msg


def test_version_constant_is_simple_string():
    assert isinstance(PROTOCOL_VERSION, str) and PROTOCOL_VERSION.strip() != ""

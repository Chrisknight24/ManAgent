"""Tests routage chat C2 : forced optionnels, auto-route par capabilities."""
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.provider_manager import (
    ProviderManager,
    ModelMetadata,
    resolve_chat_model,
)


class _StubProvider:
    def __init__(self, pid):
        self.provider_id = pid
        self.provider_name = pid

    def has_available_keys(self):
        return True


def _manager_with_text_model():
    mgr = ProviderManager()
    mgr.register_provider(_StubProvider("gemini"))
    mgr.register_model_metadata(ModelMetadata(
        model_id="gemini-3.5-flash-lite",
        provider_id="gemini",
        display_name="Lite",
        capabilities=["text"],
    ))
    return mgr


def test_forced_both_returns_as_is():
    mgr = ProviderManager()
    assert resolve_chat_model("groq", "llama-x", mgr) == ("groq", "llama-x")


def test_missing_both_auto_routes_text_model():
    mgr = _manager_with_text_model()
    assert resolve_chat_model("", "", mgr) == ("gemini", "gemini-3.5-flash-lite")


def test_partial_forced_uses_preference():
    mgr = _manager_with_text_model()
    assert resolve_chat_model("gemini", "", mgr) == ("gemini", "gemini-3.5-flash-lite")


def test_empty_registry_raises_clear_error():
    with pytest.raises(ValueError, match="capacité 'text'"):
        resolve_chat_model("", "", ProviderManager())

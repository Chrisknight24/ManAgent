"""Failover Gemini : un appel ne peut plus geler la mission 296 s.

Incident du 27/10 : silence de 02:34:45 a 02:39:41 sans log, coherent avec
un appel HTTP sans timeout (`_execute_with_failover`). Les cooldowns
n'expliquent pas ce gel (6 x 503 en 46 s seulement).

On teste ici le contrat : budget par appel + coupe-circuit global, et le
diagnostic de fin (finish_reason / jetons de raisonnement) qui permettra de
trancher la troncature JSON.
"""

import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.base_provider import ProviderServiceUnavailableError
from providers.gemini_provider import GeminiProvider


class _FakeProvider:
    def __init__(self, timeout=0.05, budget=0.5, keys=1):
        self.api_keys_pool = [{"key": f"k{i}"} for i in range(keys)]
        self.client = None
        self._current_client_key = None
        self.call_timeout_seconds = timeout
        self.failover_budget_seconds = budget
        self.cooldowns = []

    def check_cancelled(self):
        return None

    def get_active_api_key(self):
        return "k0"

    async def _get_or_create_client(self):
        return object()

    def promote_key(self, key):
        return None

    def note_rate_limit(self, key, retry_after=None):
        return 0.0

    def mark_key_in_cooldown(self, key, cooldown_seconds=0.0):
        self.cooldowns.append((key, cooldown_seconds))

    def has_available_keys(self):
        return False


@pytest.mark.asyncio
async def test_hanging_call_is_bounded_by_the_per_call_timeout():
    fake = _FakeProvider(timeout=0.05, budget=0.5)

    async def hang():
        await asyncio.sleep(10)

    started = time.monotonic()
    with pytest.raises(ProviderServiceUnavailableError):
        await GeminiProvider._execute_with_failover(fake, hang)
    assert time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_global_budget_cuts_the_failover_loop():
    fake = _FakeProvider(timeout=0.05, budget=0.12, keys=3)

    async def hang():
        await asyncio.sleep(10)

    started = time.monotonic()
    with pytest.raises(ProviderServiceUnavailableError):
        await GeminiProvider._execute_with_failover(fake, hang)
    assert time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_fast_call_is_returned_unchanged():
    fake = _FakeProvider(timeout=1.0, budget=2.0)

    async def ok():
        return "result"

    assert await GeminiProvider._execute_with_failover(fake, ok) == "result"


def test_finish_diagnostics_reports_truncation_and_thoughts():
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        usage_metadata=SimpleNamespace(thoughts_token_count=7000, candidates_token_count=900),
    )
    diag = GeminiProvider.finish_diagnostics(response)
    assert diag["finish_reason"] == "MAX_TOKENS"
    assert diag["thoughts_tokens"] == 7000
    assert diag["output_tokens"] == 900
    assert diag["truncated"] is True


def test_finish_diagnostics_is_robust_to_missing_fields():
    diag = GeminiProvider.finish_diagnostics(SimpleNamespace())
    assert diag["finish_reason"] is None
    assert diag["truncated"] is False
    assert diag["thoughts_tokens"] is None

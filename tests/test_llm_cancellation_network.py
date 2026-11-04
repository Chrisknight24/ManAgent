"""Annulation en vol et classification réseau des appels LLM."""
import asyncio
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import Llm, _is_network_error


def test_await_cancellable_interrupts_inflight_call():
    llm = Llm.__new__(Llm)
    llm.runtime_state = SimpleNamespace(cancel_requested=False, generation_epoch=0)

    async def slow():
        await asyncio.sleep(5)
        return "late"

    async def scenario():
        task = asyncio.create_task(llm._await_cancellable(slow(), 0))
        await asyncio.sleep(0.15)
        llm.runtime_state.cancel_requested = True
        start = time.monotonic()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return time.monotonic() - start

    elapsed = asyncio.run(scenario())
    assert elapsed < 1.0


def test_epoch_change_interrupts_inflight_call():
    llm = Llm.__new__(Llm)
    llm.runtime_state = SimpleNamespace(cancel_requested=False, generation_epoch=0)

    async def slow():
        await asyncio.sleep(5)
        return "late"

    async def scenario():
        task = asyncio.create_task(llm._await_cancellable(slow(), 0))
        await asyncio.sleep(0.15)
        llm.runtime_state.generation_epoch = 1
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_network_error_detection_direct_and_nested():
    assert _is_network_error(OSError("getaddrinfo failed"))
    assert _is_network_error(ConnectionRefusedError("connection refused"))
    assert not _is_network_error(ValueError("invalid JSON"))

    outer = RuntimeError("provider failed")
    outer.__cause__ = OSError("temporary failure in name resolution")
    assert _is_network_error(outer)

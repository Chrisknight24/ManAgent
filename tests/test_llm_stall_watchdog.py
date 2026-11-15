"""Battement de vie d'un appel LLM (`llm_call.stall`).

Vecu : un appel muet laisse l'utilisateur devant dix minutes de silence, sans
aucune ligne pour distinguer « le fournisseur travaille » de « il ne repondra
jamais ». Le battement rend l'attente visible.

Trois garanties, testees ici :
1. il n'interrompt rien et ne raccourcit **jamais** le budget de la classe ;
2. il est configurable par l'hote, et peut etre desactive explicitement ;
3. il ne change pas ce que l'appelant recoit (resultat ou exception).
"""

import asyncio
import os
import sys
import types
from typing import Optional

import pytest
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import Llm
from core.llm_policy import (
    DEFAULT_STALL_INTERVAL_SECONDS,
    MAX_STALL_INTERVAL_SECONDS,
    MIN_STALL_INTERVAL_SECONDS,
    policy_from_mapping,
    stall_interval_for,
)
from utils.logger import Logger


class _Answer(BaseModel):
    ok: bool = True


class _FakeProvider:
    """Provider minimal : il attend, puis rend une reponse valide."""

    def __init__(self, delay_s: float = 0.0, fail: Optional[Exception] = None):
        self.delay_s = delay_s
        self.fail = fail
        self.model_name = "fake-model"
        self.runtime_state = None
        self.call_epoch = 0

    async def generate_structured_output(self, prompt, response_schema, context=None,
                                         media_assets=None, max_output_tokens=None):
        await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        return response_schema()


class _FakeProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, provider_id):
        return self._provider


def _runtime(stall_interval_s=None, cancel=False, epoch=1):
    manifest = None
    if stall_interval_s is not None:
        manifest = types.SimpleNamespace(
            metadata={"llm_policy": {"stall_interval_s": stall_interval_s}},
            environment=None,
        )
    return types.SimpleNamespace(
        host_manifest=manifest,
        generation_epoch=epoch,
        cancel_requested=cancel,
        cancel_requested_for_turn=False,
        execution_context={"mission_id": "m1", "solver_id": "s1"},
    )


def _llm(provider, runtime_state):
    return Llm(
        _FakeProviderManager(provider),
        "fake-provider",
        "fake-model",
        runtime_state=runtime_state,
    )


@pytest.fixture
def events(monkeypatch):
    seen = []

    def _event(event_type, **fields):
        seen.append((event_type, fields))

    monkeypatch.setattr(Logger, "event", staticmethod(_event))
    return seen


def _stalls(events):
    return [f for k, f in events if k == "llm_call.stall"]


# --------------------------------------------------------------------------
# Reglage (agnostique, cote hote)
# --------------------------------------------------------------------------

def test_defaut_30_secondes():
    assert DEFAULT_STALL_INTERVAL_SECONDS == 30.0
    assert stall_interval_for(None) == 30.0


def test_l_hote_peut_regler_l_intervalle():
    policy = policy_from_mapping({"stall_interval_s": 45})
    assert policy.stall_interval_s == 45.0


def test_l_hote_peut_desactiver(monkeypatch):
    for off in (0, "0", False, "off", "none", "disabled"):
        policy = policy_from_mapping({"stall_interval_s": off})
        assert policy.stall_interval_s == 0.0, off


@pytest.mark.parametrize("value,attendu", [
    (0.01, MIN_STALL_INTERVAL_SECONDS),
    (10_000, MAX_STALL_INTERVAL_SECONDS),
    ("abc", DEFAULT_STALL_INTERVAL_SECONDS),
    (None, DEFAULT_STALL_INTERVAL_SECONDS),
    (float("nan"), DEFAULT_STALL_INTERVAL_SECONDS),
])
def test_intervalle_borne_et_tolerant(value, attendu):
    assert policy_from_mapping({"stall_interval_s": value}).stall_interval_s == attendu


def test_une_valeur_inconnue_ne_casse_pas_la_politique():
    policy = policy_from_mapping({"stall_interval_s": {"x": 1}, "profiles": None})
    assert policy.stall_interval_s == DEFAULT_STALL_INTERVAL_SECONDS


# --------------------------------------------------------------------------
# Battement : il signale, il ne coupe pas
# --------------------------------------------------------------------------

def test_le_battement_signale_un_appel_qui_traine(events):
    provider = _FakeProvider(delay_s=2.4)
    llm = _llm(provider, _runtime(stall_interval_s=1))

    result = asyncio.run(
        llm.generate_structured("prompt", _Answer, with_discovery=False,
                                call_kind="selector")
    )

    assert result.ok is True
    stalls = _stalls(events)
    assert len(stalls) >= 2, stalls
    assert [s["tick"] for s in stalls] == sorted(s["tick"] for s in stalls)
    first = stalls[0]
    assert first["kind"] == "selector"
    assert first["budget_s"] == 120.0
    assert first["elapsed_s"] >= 1.0
    assert first["mission_id"] == "m1"


def test_le_battement_ne_raccourcit_jamais_le_budget(events):
    provider = _FakeProvider(delay_s=0.05)
    llm = _llm(provider, _runtime(stall_interval_s=1))

    asyncio.run(
        llm.generate_structured("prompt", _Answer, with_discovery=False,
                                call_kind="analysis")
    )

    # Le profil de la classe reste la seule autorite : 300 s pour une analyse.
    assert provider.call_timeout_seconds == 300.0
    assert provider.failover_budget_seconds == 300.0


def test_aucun_battement_quand_l_hote_le_desactive(events):
    provider = _FakeProvider(delay_s=0.3)
    llm = _llm(provider, _runtime(stall_interval_s=0))

    asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))

    assert _stalls(events) == []


def test_aucun_battement_pour_un_appel_rapide(events):
    provider = _FakeProvider(delay_s=0.05)
    llm = _llm(provider, _runtime(stall_interval_s=1))

    asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))

    assert _stalls(events) == []


def test_le_battement_ne_mange_pas_l_exception(events):
    provider = _FakeProvider(delay_s=0.05, fail=ValueError("boum"))
    llm = _llm(provider, _runtime(stall_interval_s=1))

    with pytest.raises(ValueError):
        asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))


def test_l_annulation_en_vol_reste_effective(events):
    """Le battement ne doit pas retarder ni empecher l'annulation."""
    async def scenario():
        provider = _FakeProvider(delay_s=5.0)
        rs = _runtime(stall_interval_s=1, epoch=1)
        llm = _llm(provider, rs)

        async def _stop_soon():
            await asyncio.sleep(0.25)
            rs.cancel_requested = True

        task = asyncio.ensure_future(
            llm._await_cancellable(
                provider.generate_structured_output("p", _Answer), 1,
                kind="default", budget_s=600,
            )
        )
        stopper = asyncio.ensure_future(_stop_soon())
        with pytest.raises(asyncio.CancelledError):
            await task
        await stopper

    asyncio.run(scenario())


def test_sans_epoch_aucune_annulation_implicite(events):
    """Les appelants historiques (sans surveillance) ne sont pas annules."""
    async def scenario():
        provider = _FakeProvider(delay_s=0.05)
        rs = _runtime(stall_interval_s=1)
        rs.cancel_requested = True  # un drapeau global traine
        rs.generation_epoch = 7
        llm = _llm(provider, rs)
        return await llm._await_cancellable(
            provider.generate_structured_output("p", _Answer), None,
        )

    assert asyncio.run(scenario()).ok is True


# --------------------------------------------------------------------------
# La doc suit le code (un reglage non documente n'existe pas pour l'hote)
# --------------------------------------------------------------------------

def test_la_doc_decrit_le_nouveau_reglage():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("docs/HOST_CONTRACT.md", "docs/en/HOST_CONTRACT.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "stall_interval_s" in text, rel
        assert "llm_call.stall" in text, rel
        assert str(int(DEFAULT_STALL_INTERVAL_SECONDS)) in text, rel

    schema = json.loads((root / "docs/host.manifest.schema.json").read_text(encoding="utf-8"))
    policy = schema["properties"]["metadata"]["properties"]["llm_policy"]
    assert "stall_interval_s" in policy["properties"]
    assert "world_snapshot" in schema["properties"]["metadata"]["properties"]

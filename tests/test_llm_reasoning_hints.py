"""Indices neutres de raisonnement (`thinking_level` / `reasoning_effort`).

L'hote peut demander « reflechis plus » sur une classe d'appel precise, sans
connaitre le vocabulaire d'aucun fournisseur. Trois garanties, testees ici :

1. la declaration est validee dans un vocabulaire **neutre** ; une valeur
   inconnue est ignoree (log), jamais traduite en douce ;
2. une absence de declaration reste une absence : aucun defaut n'est invente ;
3. ce qui est demande est visible : attributs poses sur le provider et
   evenement `llm_call` coherent, y compris quand le provider ne sait pas
   (encore) traduire les indices.
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
    REASONING_EFFORTS,
    THINKING_LEVELS,
    policy_from_mapping,
    profile_for,
)
from providers.base_provider import BaseProvider
from utils.logger import Logger


class _Answer(BaseModel):
    ok: bool = True


class _PlainProvider:
    """Provider minimal, qui ne declare aucune traduction native."""

    def __init__(self, fail: Optional[Exception] = None):
        self.fail = fail
        self.provider_id = "plain-provider"
        self.model_name = "plain-model"
        self.runtime_state = None
        self.call_epoch = 0

    async def generate_structured_output(self, prompt, response_schema, context=None,
                                         media_assets=None, max_output_tokens=None):
        if self.fail is not None:
            raise self.fail
        return response_schema()


class _NativeProvider(_PlainProvider):
    """Provider qui declare savoir traduire les indices."""

    SUPPORTS_REASONING_HINTS = True


class _FakeProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, provider_id):
        return self._provider


def _runtime(policy=None, epoch=1):
    manifest = None
    if policy is not None:
        manifest = types.SimpleNamespace(metadata={"llm_policy": policy}, environment=None)
    return types.SimpleNamespace(
        host_manifest=manifest,
        generation_epoch=epoch,
        cancel_requested=False,
        cancel_requested_for_turn=False,
        execution_context={"mission_id": "m1", "solver_id": "s1"},
    )


def _llm(provider, runtime_state):
    return Llm(
        _FakeProviderManager(provider),
        "plain-provider",
        "plain-model",
        runtime_state=runtime_state,
    )


@pytest.fixture
def events(monkeypatch):
    seen = []

    def _event(event_type, **fields):
        seen.append((event_type, fields))

    monkeypatch.setattr(Logger, "event", staticmethod(_event))
    return seen


@pytest.fixture
def infos(monkeypatch):
    seen = []

    def _info(message, *args, **kwargs):
        seen.append(str(message))

    monkeypatch.setattr(Logger, "info", staticmethod(_info))
    return seen


@pytest.fixture
def debugs(monkeypatch):
    seen = []

    def _debug(message, *args, **kwargs):
        seen.append(str(message))

    monkeypatch.setattr(Logger, "debug", staticmethod(_debug))
    return seen


# --------------------------------------------------------------------------
# Vocabulaire : neutre, borne, tolerant
# --------------------------------------------------------------------------

def test_le_vocabulaire_neutre_est_le_meme_pour_les_deux_indices():
    assert THINKING_LEVELS == ("minimal", "low", "medium", "high")
    assert REASONING_EFFORTS == THINKING_LEVELS


@pytest.mark.parametrize("champ,valeur", [
    ("thinking_level", "high"),
    ("thinking_level", "MINIMAL"),
    ("thinking_level", "  Low  "),
    ("reasoning_effort", "medium"),
])
def test_une_valeur_du_vocabulaire_est_conservee(champ, valeur):
    policy = policy_from_mapping({"profiles": {"analysis": {champ: valeur}}})
    profile = policy.profile_for("analysis")
    assert getattr(profile, champ) == valeur.strip().lower()


@pytest.mark.parametrize("champ", ["thinking_level", "reasoning_effort"])
@pytest.mark.parametrize("valeur", ["turbo", "very-high", 3, {"level": "high"}, [], True])
def test_une_valeur_inconnue_est_ignoree_sans_casser(champ, valeur, infos):
    policy = policy_from_mapping({"profiles": {"analysis": {champ: valeur}}})
    profile = policy.profile_for("analysis")
    assert getattr(profile, champ) == "", valeur


def test_une_valeur_inconnue_est_signalee_pas_devinee(infos):
    policy_from_mapping({"profiles": {"analysis": {"thinking_level": "turbo"}}})
    assert any("thinking_level" in message and "ignore" in message for message in infos), infos


@pytest.mark.parametrize("champ", ["thinking_level", "reasoning_effort"])
def test_une_absence_reste_une_absence(champ):
    policy = policy_from_mapping({"profiles": {"analysis": {"total_s": 300}}})
    assert getattr(policy.profile_for("analysis"), champ) == ""


def test_les_indices_sont_par_classe():
    policy = policy_from_mapping({"profiles": {
        "analysis": {"thinking_level": "high"},
        "selector": {"thinking_level": "low", "reasoning_effort": "minimal"},
    }})
    assert policy.profile_for("analysis").thinking_level == "high"
    assert policy.profile_for("analysis").reasoning_effort == ""
    assert policy.profile_for("selector").thinking_level == "low"
    assert policy.profile_for("selector").reasoning_effort == "minimal"


def test_un_profil_numerique_n_invente_pas_d_indice():
    policy = policy_from_mapping({"profiles": {"analysis": 300}})
    profile = policy.profile_for("analysis")
    assert profile.thinking_level == ""
    assert profile.reasoning_effort == ""


def test_un_profil_absent_herite_des_indices_du_defaut():
    policy = policy_from_mapping({"profiles": {"default": {"thinking_level": "medium"}}})
    assert profile_for(_runtime({"profiles": {"default": {"thinking_level": "medium"}}}), "inconnu").thinking_level == "medium"
    assert policy.profile_for("inconnu").thinking_level == "medium"


# --------------------------------------------------------------------------
# Transmission au provider
# --------------------------------------------------------------------------

def test_les_indices_sont_poses_sur_le_provider():
    provider = _PlainProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {
        "total_s": 300, "thinking_level": "high", "reasoning_effort": "low",
    }}}))

    llm._apply_call_policy(provider, "analysis")

    assert provider.call_thinking_level == "high"
    assert provider.call_reasoning_effort == "low"
    assert provider.call_timeout_seconds == 300.0


def test_une_absence_laisse_le_provider_sans_indice():
    provider = _PlainProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {"total_s": 300}}}))

    llm._apply_call_policy(provider, "analysis")

    assert provider.call_thinking_level == ""
    assert provider.call_reasoning_effort == ""


def test_un_provider_natif_ne_declenche_pas_l_avertissement(debugs):
    provider = _NativeProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {"thinking_level": "high"}}}))

    llm._apply_call_policy(provider, "analysis")

    assert not any("sans traduction native" in message for message in debugs), debugs
    assert provider.call_thinking_level == "high"


def test_un_provider_sans_traduction_est_dit_explicitement(debugs):
    provider = _PlainProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {"thinking_level": "high"}}}))

    llm._apply_call_policy(provider, "analysis")

    assert any("sans traduction native" in message for message in debugs), debugs
    # Le reglage reste pose : c'est le provider qui choisit de l'ignorer.
    assert provider.call_thinking_level == "high"


def test_la_base_declare_l_absence_de_traduction_par_defaut():
    assert BaseProvider.SUPPORTS_REASONING_HINTS is False


# --------------------------------------------------------------------------
# Observabilite : llm_call dit ce qui a ete demande
# --------------------------------------------------------------------------

def _llm_calls(events):
    return [fields for kind, fields in events if kind == "llm_call"]


def test_l_evenement_porte_les_indices_demandes(events):
    provider = _PlainProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {
        "thinking_level": "high", "reasoning_effort": "medium",
    }}}))

    asyncio.run(
        llm.generate_structured("prompt", _Answer, with_discovery=False,
                                call_kind="analysis")
    )

    calls = _llm_calls(events)
    assert calls, events
    assert calls[-1]["thinking_level"] == "high"
    assert calls[-1]["reasoning_effort"] == "medium"
    # Et l'effet est reellement demande au provider, pas seulement journalise.
    assert provider.call_thinking_level == "high"


def test_l_evenement_ne_porte_rien_quand_l_hote_ne_demande_rien(events):
    provider = _PlainProvider()
    llm = _llm(provider, _runtime({"profiles": {"analysis": {"total_s": 300}}}))

    asyncio.run(
        llm.generate_structured("prompt", _Answer, with_discovery=False,
                                call_kind="analysis")
    )

    calls = _llm_calls(events)
    assert calls, events
    assert "thinking_level" not in calls[-1]
    assert "reasoning_effort" not in calls[-1]


def test_l_evenement_d_erreur_porte_aussi_les_indices(events):
    provider = _PlainProvider(fail=ValueError("boum"))
    llm = _llm(provider, _runtime({"profiles": {"analysis": {"thinking_level": "low"}}}))

    with pytest.raises(ValueError):
        asyncio.run(
            llm.generate_structured("prompt", _Answer, with_discovery=False,
                                    call_kind="analysis")
        )

    calls = _llm_calls(events)
    assert calls, events
    assert calls[-1]["thinking_level"] == "low"
    assert calls[-1]["success"] is False


# --------------------------------------------------------------------------
# La doc suit le code (un reglage non documente n'existe pas pour l'hote)
# --------------------------------------------------------------------------

def test_la_doc_et_le_schema_decrivent_le_vocabulaire():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("docs/HOST_CONTRACT.md", "docs/en/HOST_CONTRACT.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "thinking_level" in text, rel
        assert "reasoning_effort" in text, rel
        assert "minimal" in text and "high" in text, rel
        assert "llm_call" in text, rel

    schema = json.loads((root / "docs/host.manifest.schema.json").read_text(encoding="utf-8"))
    profile = schema["properties"]["metadata"]["properties"]["llm_policy"]["properties"]["profiles"]
    entries = profile["additionalProperties"]["oneOf"]
    obj = next(item for item in entries if item.get("type") == "object")
    for champ in ("thinking_level", "reasoning_effort"):
        assert obj["properties"][champ]["enum"] == ["minimal", "low", "medium", "high"]

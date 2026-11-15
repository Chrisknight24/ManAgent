"""Streaming des reponses structurees : le contrat de sortie ne bouge pas.

Le provider lisait la reponse complete avant de parser : sur 90 s de latence,
rien n'etait mesurable. Ici, le flux natif est consomme pour horodater le
**premier contenu** (`llm_call.first_byte_ms`), sans changer ce que recoit
l'appelant (toujours l'objet Pydantic valide).

Ce banc est volontairement brutal : il simule le SSE, les chunks coupes, les
lignes illisibles, les erreurs serveur, la rotation de cles, et le coupe-circuit
hote (`stream_structured: false`). Aucune requete reseau reelle.
"""

import asyncio
import json
import os
import sys
import types
from typing import Optional

import aiohttp
import pytest
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers.openai_compatible as compatible
from core.llm import Llm
from core.llm_policy import (
    DEFAULT_STREAM_STRUCTURED,
    policy_from_mapping,
    stream_structured_for,
)
from providers.base_provider import (
    ProviderAuthError,
    ProviderError,
    ProviderQuotaExhaustedError,
    ProviderServiceUnavailableError,
)
from providers.openai_compatible import accumulate_json_stream, stream_json_completion
from providers.openai_provider import OpenAIProvider
from providers.openrouter_provider import OpenRouterProvider
from providers.groq_provider import GroqProvider
from providers.deepseek_provider import DeepSeekProvider
from utils.logger import Logger


# Les quatre providers « OpenAI-compatible » partagent le meme format de flux.
# Chacun garde son endpoint et son label : le test le verifie explicitement.
TWINS = [
    (OpenAIProvider, "https://api.openai.com/v1/chat/completions"),
    (OpenRouterProvider, "https://openrouter.ai/api/v1/chat/completions"),
    (GroqProvider, "https://api.groq.com/openai/v1/chat/completions"),
    (DeepSeekProvider, "https://api.deepseek.com/chat/completions"),
]


class _Answer(BaseModel):
    ok: bool = True


# --------------------------------------------------------------------------
# Faux transport : un flux SSE, pas de reseau
# --------------------------------------------------------------------------

class _LineStream:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeResponse:
    def __init__(self, status=200, lines=None, text="", headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = _LineStream(lines or [])
        self._text = text

    async def text(self):
        return self._text


class _FakePost:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, *exc):
        return False


class _Transport:
    """File de reponses + journal des requetes envoyees."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def install(self, monkeypatch):
        transport = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def post(self, endpoint, headers=None, json=None):
                transport.requests.append({
                    "endpoint": endpoint,
                    "headers": dict(headers or {}),
                    "json": json,
                })
                item = transport.responses.pop(0)
                if isinstance(item, Exception):
                    return _FakePost(error=item)
                return _FakePost(response=item)

        monkeypatch.setattr(compatible.aiohttp, "ClientSession", _Session)
        monkeypatch.setattr(compatible.asyncio, "sleep", _no_sleep)
        return transport


async def _no_sleep(*args, **kwargs):
    return None


def _sse(*payloads):
    lines = []
    for payload in payloads:
        if isinstance(payload, str):
            lines.append(f"data: {payload}\n")
        else:
            lines.append("data: " + json.dumps(payload) + "\n")
    return lines


def _content(text):
    return {"choices": [{"delta": {"content": text}}]}


def _usage(total=5):
    return {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": total}}


def _provider(keys=("k1",), model="gpt-test"):
    return OpenAIProvider(api_keys=list(keys), model_name=model, system_prompt="system")


# --------------------------------------------------------------------------
# Accumulateur pur
# --------------------------------------------------------------------------

def test_accumule_le_texte_et_horodate_le_premier_contenu():
    ticks = iter([100.0, 101.0])
    seen = []

    async def scenario():
        lines = _LineStream(_sse(
            {"choices": [{"delta": {"role": "assistant"}}]},
            "",
            "not-sse",
            _content('{"ok":'),
            _content(" true}"),
            _usage(),
            "[DONE]",
        ))
        return await accumulate_json_stream(
            lines,
            on_first_content=lambda: seen.append("first"),
            clock=lambda: next(ticks),
        )

    outcome = asyncio.run(scenario())
    assert outcome.text == '{"ok": true}'
    assert outcome.first_content_at == 100.0
    assert outcome.chunks == 2
    assert outcome.done is True
    assert outcome.usage["total_tokens"] == 5
    assert seen == ["first"]


def test_un_chunk_d_usage_ne_compte_pas_comme_premier_contenu():
    async def scenario():
        return await accumulate_json_stream(_LineStream(_sse(_usage(), "[DONE]")))

    outcome = asyncio.run(scenario())
    assert outcome.text == ""
    assert outcome.first_content_at is None
    assert outcome.usage["total_tokens"] == 5


def test_une_ligne_illisible_est_ignoree_et_comptee():
    async def scenario():
        return await accumulate_json_stream(_LineStream([
            "data: {ceci n'est pas du json}\n",
            "data: \n",
            "data: " + json.dumps(_content('{"ok":')) + "\n",
            "data: " + json.dumps(_content(" true}")) + "\n",
            "[DONE]\n",
        ]))

    outcome = asyncio.run(scenario())
    assert outcome.text == '{"ok": true}'
    assert outcome.malformed_lines == 1
    assert outcome.done is False  # sans prefixe data:, la sentinelle est ignoree


def test_les_lignes_en_bytes_sont_acceptees():
    async def scenario():
        return await accumulate_json_stream(_LineStream([
            b'data: {"choices":[{"delta":{"content":"{}"}}]}\n',
            b"data: [DONE]\n",
        ]))

    outcome = asyncio.run(scenario())
    assert outcome.text == "{}"
    assert outcome.done is True


def test_une_erreur_serveur_dans_le_flux_leve():
    async def scenario():
        return await accumulate_json_stream(_LineStream(_sse({"error": {"message": "boom"}})))

    with pytest.raises(ProviderError):
        asyncio.run(scenario())


# --------------------------------------------------------------------------
# Appel provider complet
# --------------------------------------------------------------------------

def _structured_run(provider, monkeypatch, transport):
    transport.install(monkeypatch)

    async def scenario():
        return await provider.generate_structured_output("prompt", _Answer)

    return asyncio.run(scenario())


def test_le_provider_lit_le_flux_et_rend_le_meme_objet(monkeypatch):
    provider = _provider()
    transport = _Transport([_FakeResponse(lines=_sse(
        _content('{"ok":'),
        _content(" true}"),
        _usage(),
        "[DONE]",
    ))])

    result = _structured_run(provider, monkeypatch, transport)

    assert isinstance(result, _Answer)
    assert result.ok is True
    assert provider._first_byte_marked is True
    assert provider.get_last_usage()["total_tokens"] == 5
    sent = transport.requests[0]
    assert sent["json"]["stream"] is True
    assert sent["json"]["response_format"] == {"type": "json_object"}
    assert sent["headers"]["Authorization"] == "Bearer k1"


def test_le_coupe_circuit_hote_revient_au_chemin_classique(monkeypatch):
    provider = _provider()
    provider.call_stream_structured = False
    calls = []

    async def _non_stream(payload, endpoint=None):
        calls.append(payload)
        return {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(provider, "_execute_http_request", _non_stream)
    transport = _Transport([])
    transport.install(monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert calls and calls[0].get("stream") is False
    assert transport.requests == []
    assert provider.get_last_usage()["total_tokens"] == 2


def test_un_429_fait_tourner_la_cle_puis_reussit(monkeypatch):
    provider = _provider(keys=("k1", "k2"))
    transport = _Transport([
        _FakeResponse(status=429, text="rate limit exceeded"),
        _FakeResponse(lines=_sse(_content('{"ok": true}'), "[DONE]")),
    ])

    result = _structured_run(provider, monkeypatch, transport)

    assert result.ok is True
    assert len(transport.requests) == 2
    assert transport.requests[0]["headers"]["Authorization"] == "Bearer k1"
    assert transport.requests[1]["headers"]["Authorization"] == "Bearer k2"


def test_un_429_sur_la_derniere_cle_leve_un_quota(monkeypatch):
    provider = _provider(keys=("k1",))
    transport = _Transport([_FakeResponse(status=429, text="rate limit")])

    with pytest.raises(ProviderQuotaExhaustedError):
        _structured_run(provider, monkeypatch, transport)


def test_un_401_leve_une_erreur_d_authentification(monkeypatch):
    provider = _provider()
    transport = _Transport([_FakeResponse(status=401, text="invalid key")])

    with pytest.raises(ProviderAuthError):
        _structured_run(provider, monkeypatch, transport)
    assert len(transport.requests) == 1


def test_une_erreur_reseau_est_typée(monkeypatch):
    provider = _provider()
    transport = _Transport([aiohttp.ClientError("boom")])

    with pytest.raises(ProviderServiceUnavailableError):
        _structured_run(provider, monkeypatch, transport)


def test_un_flux_vide_echoue_comme_une_reponse_vide(monkeypatch):
    provider = _provider()
    transport = _Transport([_FakeResponse(lines=_sse("[DONE]"))])

    with pytest.raises(json.JSONDecodeError):
        _structured_run(provider, monkeypatch, transport)


@pytest.mark.parametrize("provider_cls,endpoint", TWINS)
def test_chaque_jumeau_lit_son_flux_et_garde_son_endpoint(provider_cls, endpoint, monkeypatch):
    provider = provider_cls(api_keys=["k1"], model_name="modele-test", system_prompt="systeme")
    transport = _Transport([_FakeResponse(lines=_sse(
        _content('{"ok":'),
        _content(" true}"),
        "[DONE]",
    ))])

    result = _structured_run(provider, monkeypatch, transport)

    assert isinstance(result, _Answer) and result.ok is True
    assert provider._first_byte_marked is True
    assert transport.requests[0]["endpoint"] == endpoint
    assert transport.requests[0]["json"]["stream"] is True


@pytest.mark.parametrize("provider_cls,_endpoint", TWINS)
def test_chaque_jumeau_declare_la_capacite_de_flux(provider_cls, _endpoint):
    assert provider_cls.STREAMS_STRUCTURED is True


def test_aucune_cle_disponible_leve_un_quota():
    provider = _provider(keys=())

    async def scenario():
        return await stream_json_completion(
            provider, endpoint="https://example.invalid", payload={}, label="Test"
        )

    with pytest.raises(ProviderQuotaExhaustedError):
        asyncio.run(scenario())


# --------------------------------------------------------------------------
# Le cerveau mesure et publie
# --------------------------------------------------------------------------

class _StreamingFakeProvider:
    STREAMS_STRUCTURED = True

    def __init__(self, mark: bool = True, delay_s: float = 0.05):
        self.mark = mark
        self.delay_s = delay_s
        self.provider_id = "fake"
        self.model_name = "fake-model"
        self.runtime_state = None
        self.call_epoch = 0

    async def generate_structured_output(self, prompt, response_schema, context=None,
                                         media_assets=None, max_output_tokens=None):
        await asyncio.sleep(self.delay_s)
        if self.mark:
            self._mark_first_byte()
        return response_schema()

    def _mark_first_byte(self):
        if getattr(self, "_first_byte_marked", False):
            return
        self._first_byte_marked = True
        hook = getattr(self, "call_first_byte_hook", None)
        if callable(hook):
            hook()


class _FakeProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, provider_id):
        return self._provider


def _runtime(policy=None):
    manifest = None
    if policy is not None:
        manifest = types.SimpleNamespace(metadata={"llm_policy": policy}, environment=None)
    return types.SimpleNamespace(
        host_manifest=manifest,
        generation_epoch=1,
        cancel_requested=False,
        cancel_requested_for_turn=False,
        execution_context={"mission_id": "m1"},
    )


def _llm(provider, runtime_state):
    return Llm(_FakeProviderManager(provider), "fake", "fake-model",
               runtime_state=runtime_state)


@pytest.fixture
def events(monkeypatch):
    seen = []

    def _event(event_type, **fields):
        seen.append((event_type, fields))

    monkeypatch.setattr(Logger, "event", staticmethod(_event))
    return seen


def _calls(events):
    return [fields for kind, fields in events if kind == "llm_call"]


def test_l_evenement_porte_first_byte_ms(events):
    llm = _llm(_StreamingFakeProvider(), _runtime())

    asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))

    call = _calls(events)[-1]
    assert isinstance(call["first_byte_ms"], int)
    assert 0 <= call["first_byte_ms"] <= call["duration_ms"]


def test_sans_observation_aucun_first_byte_invente(events):
    llm = _llm(_StreamingFakeProvider(mark=False), _runtime())

    asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))

    assert "first_byte_ms" not in _calls(events)[-1]


def test_l_hote_peut_couper_le_flux(events):
    provider = _StreamingFakeProvider()
    runtime = _runtime({"stream_structured": False})
    llm = _llm(provider, runtime)

    asyncio.run(llm.generate_structured("prompt", _Answer, with_discovery=False))

    assert provider.call_stream_structured is False


def test_le_flux_est_actif_par_defaut():
    assert DEFAULT_STREAM_STRUCTURED is True
    assert policy_from_mapping({}).stream_structured is True
    assert stream_structured_for(_runtime()) is True


@pytest.mark.parametrize("valeur,attendu", [
    (False, False), ("false", False), ("off", False), (0, False), ("no", False),
    (True, True), ("true", True), ("on", True), (1, True), ("yes", True),
    ("illisible", True), (None, True), ({}, True),
])
def test_lecture_tolerante_du_reglage(valeur, attendu):
    assert policy_from_mapping({"stream_structured": valeur}).stream_structured is attendu


# --------------------------------------------------------------------------
# La doc suit le code
# --------------------------------------------------------------------------

def test_la_doc_et_le_schema_decrivent_le_reglage():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("docs/HOST_CONTRACT.md", "docs/en/HOST_CONTRACT.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "stream_structured" in text, rel
        assert "first_byte_ms" in text, rel

    schema = json.loads((root / "docs/host.manifest.schema.json").read_text(encoding="utf-8"))
    policy = schema["properties"]["metadata"]["properties"]["llm_policy"]
    assert "stream_structured" in policy["properties"]

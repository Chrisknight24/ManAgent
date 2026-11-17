"""Flux Anthropic et Gemini : formats differents, memes garanties.

Anthropic renvoie les arguments d'un outil force par fragments
(`input_json_delta`) ; Gemini renvoie des chunks SDK. Dans les deux cas, le
contrat de sortie ne change pas (objet Pydantic valide) et le premier contenu
est horodate.

On verifie aussi le garde-fou le plus important : **aucun rejeu silencieux**
apres avoir *livre* du contenu au consommateur. Un flux interrompu doit echouer
franchement.

Mais une reponse structuree accumulee en memoire n'est pas « livree » : la
sortie partielle est jetee avec le buffer, donc la rejouer sur la cle suivante
est sans consequence. Confondre les deux transformait un 503 de surcharge en
mission morte (run 16/11 : abandon a l'essai 1/9, huit cles jamais essayees).
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import aiohttp
import pytest
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers.sse_transport as sse_transport
from providers.anthropic_provider import AnthropicProvider
from providers.anthropic_stream import accumulate_tool_use_stream, StreamedToolUse
from providers.base_provider import ProviderError, ProviderServiceUnavailableError
from providers.gemini_provider import GeminiProvider
from providers.openai_compatible import accumulate_json_stream


class _Answer(BaseModel):
    ok: bool = True


# --------------------------------------------------------------------------
# Transport commun simule
# --------------------------------------------------------------------------

class _LineStream:
    def __init__(self, lines, raise_after=None, error=None):
        self._lines = list(lines)
        self._raise_after = raise_after
        self._error = error
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_after is not None and self._index >= self._raise_after:
            raise self._error
        if not self._lines:
            raise StopAsyncIteration
        self._index += 1
        return self._lines.pop(0)


class _FakeResponse:
    def __init__(self, status=200, lines=None, text="", headers=None, raise_after=None, error=None):
        self.status = status
        self.headers = headers or {}
        self.content = _LineStream(lines or [], raise_after=raise_after, error=error)
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

        monkeypatch.setattr(sse_transport.aiohttp, "ClientSession", _Session)
        monkeypatch.setattr(sse_transport.asyncio, "sleep", _no_sleep)
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


# --------------------------------------------------------------------------
# Anthropic : fragments d'outil
# --------------------------------------------------------------------------

def _anthropic_lines(json_a='{"ok":', json_b=" true}"):
    return _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "ping"},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "record_response", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": json_a}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": json_b}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    )


def _anthropic(**kwargs):
    return AnthropicProvider(api_keys=kwargs.pop("keys", ["k1"]),
                             model_name="claude-test", system_prompt="systeme")


def test_l_accumulateur_anthropic_recolle_les_fragments():
    async def scenario():
        return await accumulate_tool_use_stream(_LineStream(_anthropic_lines()))

    outcome = asyncio.run(scenario())
    assert outcome.json_text == '{"ok": true}'
    assert outcome.fragments == 2
    assert outcome.first_content_at is not None
    assert outcome.usage == {"input_tokens": 10, "output_tokens": 7}
    assert outcome.stop_reason == "tool_use"
    assert outcome.tool_name == "record_response"
    assert outcome.done is True


def test_l_accumulateur_anthropic_ignore_le_texte_et_le_bruit():
    async def scenario():
        return await accumulate_tool_use_stream(_LineStream(_sse(
            {"type": "ping"},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "bavardage"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "message_stop"},
        )))

    outcome = asyncio.run(scenario())
    assert outcome.json_text == "{}"
    assert outcome.fragments == 1


def test_l_accumulateur_anthropic_refuse_un_mauvais_outil():
    async def scenario():
        return await accumulate_tool_use_stream(
            _LineStream(_sse(
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "tool_use", "name": "autre_outil", "input": {}}},
            )),
            expected_tool="record_response",
        )

    with pytest.raises(ProviderError):
        asyncio.run(scenario())


def test_l_accumulateur_anthropic_leve_sur_erreur_serveur():
    async def scenario():
        return await accumulate_tool_use_stream(
            _LineStream(_sse({"type": "error", "error": {"type": "overloaded_error"}}))
        )

    with pytest.raises(ProviderError):
        asyncio.run(scenario())


def test_anthropic_lit_le_flux_et_rend_le_meme_objet(monkeypatch):
    provider = _anthropic()
    transport = _Transport([_FakeResponse(lines=_anthropic_lines())])
    transport.install(monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert isinstance(result, _Answer) and result.ok is True
    assert provider._first_byte_marked is True
    assert provider.get_last_usage()["total_tokens"] == 17
    sent = transport.requests[0]
    assert sent["endpoint"] == "https://api.anthropic.com/v1/messages"
    assert sent["json"]["stream"] is True
    assert sent["json"]["tool_choice"] == {"type": "tool", "name": "record_response"}
    assert sent["headers"]["anthropic-version"] == "2023-06-01"


def test_anthropic_un_flux_vide_est_une_erreur_honnete(monkeypatch):
    provider = _anthropic()
    transport = _Transport([_FakeResponse(lines=_sse({"type": "message_stop"}))])
    transport.install(monkeypatch)

    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_structured_output("prompt", _Answer))


def test_anthropic_un_529_fait_tourner_la_cle(monkeypatch):
    provider = _anthropic(keys=["k1", "k2"])
    transport = _Transport([
        _FakeResponse(status=529, text="overloaded"),
        _FakeResponse(lines=_anthropic_lines()),
    ])
    transport.install(monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert len(transport.requests) == 2
    assert transport.requests[1]["headers"]["x-api-key"] == "k2"


def test_anthropic_un_flux_coupe_est_rejoue_sur_la_cle_suivante(monkeypatch):
    """Tampon interne interrompu -> rejeu : rien n'a ete livre."""
    provider = _anthropic(keys=["k1", "k2"])
    transport = _Transport([
        _FakeResponse(
            lines=_anthropic_lines(),
            raise_after=5,  # apres le premier fragment
            error=aiohttp.ClientError("coupure"),
        ),
        _FakeResponse(lines=_anthropic_lines()),
    ])
    transport.install(monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert len(transport.requests) == 2
    assert transport.requests[1]["headers"]["x-api-key"] == "k2"
    assert provider._first_byte_marked is True


def test_anthropic_pas_de_rejeu_apres_contenu_livre(monkeypatch):
    """Contenu deja livre au consommateur -> echec franc, aucun rejeu.

    Aucun chemin actuel ne livre un flux structure avant la fin ; ce test
    fige l'invariant pour le jour ou un tel chemin existera. Le drapeau est
    pose a la main : c'est exactement ce que ferait ce chemin.
    """
    provider = _anthropic(keys=["k1", "k2"])
    provider._content_delivered = True
    transport = _Transport([_FakeResponse(
        lines=_anthropic_lines(),
        raise_after=5,
        error=aiohttp.ClientError("coupure"),
    )])
    transport.install(monkeypatch)

    with pytest.raises(ProviderServiceUnavailableError):
        asyncio.run(provider.generate_structured_output("prompt", _Answer))
    assert len(transport.requests) == 1  # aucune reprise silencieuse
    assert provider._first_byte_marked is True


def test_anthropic_coupe_circuit_hote(monkeypatch):
    provider = _anthropic()
    provider.call_stream_structured = False
    seen = []

    async def _non_stream(payload, endpoint=None):
        seen.append(payload)
        return {
            "content": [{"type": "tool_use", "name": "record_response", "input": {"ok": True}}],
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }

    monkeypatch.setattr(provider, "_execute_http_request", _non_stream)
    transport = _Transport([])
    transport.install(monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert transport.requests == []
    assert seen and "stream" not in seen[0]
    assert provider.get_last_usage()["total_tokens"] == 5


# --------------------------------------------------------------------------
# Gemini : chunks SDK
# --------------------------------------------------------------------------

class _ChunkStream:
    def __init__(self, chunks, raise_after=None, error=None):
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self._error = error
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_after is not None and self._index >= self._raise_after:
            raise self._error
        if not self._chunks:
            raise StopAsyncIteration
        self._index += 1
        return self._chunks.pop(0)


def _chunk(text=None, usage=None):
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=[])


def _gemini_usage(total=12):
    return SimpleNamespace(prompt_token_count=5, candidates_token_count=7, total_token_count=total)


class _FakeModels:
    def __init__(self, chunks=None, response=None, raise_after=None, error=None):
        self._chunks = chunks or []
        self._response = response
        self._raise_after = raise_after
        self._error = error
        self.stream_calls = 0
        self.content_calls = 0
        self.last_config = None

    async def generate_content_stream(self, model, contents, config):
        self.stream_calls += 1
        self.last_config = config
        return _ChunkStream(self._chunks, self._raise_after, self._error)

    async def generate_content(self, model, contents, config):
        self.content_calls += 1
        self.last_config = config
        return self._response


class _FakeClient:
    def __init__(self, models):
        self.aio = SimpleNamespace(models=models)


def _gemini(models, monkeypatch, keys=("k1",)):
    provider = GeminiProvider(api_keys=list(keys), model_name="gemini-test",
                              system_prompt="systeme")

    async def _client(force_new_key=False):
        return _FakeClient(models)

    monkeypatch.setattr(provider, "_get_or_create_client", _client)
    return provider


def test_gemini_accumule_les_chunks_et_rend_le_meme_objet(monkeypatch):
    models = _FakeModels(chunks=[
        _chunk('{"ok":'),
        _chunk(" true}"),
        _chunk(None, usage=_gemini_usage()),
    ])
    provider = _gemini(models, monkeypatch)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert isinstance(result, _Answer) and result.ok is True
    assert models.stream_calls == 1
    assert models.content_calls == 0
    assert provider._first_byte_marked is True
    assert provider.get_last_usage()["total_tokens"] == 12
    assert models.last_config.response_mime_type == "application/json"


def test_gemini_coupe_circuit_hote(monkeypatch):
    response = SimpleNamespace(text='{"ok": true}', usage_metadata=_gemini_usage(), candidates=[])
    models = _FakeModels(response=response)
    provider = _gemini(models, monkeypatch, keys=("k1",))
    provider.call_stream_structured = False

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert models.stream_calls == 0
    assert models.content_calls == 1
    assert provider._first_byte_marked is False


def test_gemini_un_flux_coupe_est_rejoue_sur_la_cle_suivante(monkeypatch):
    """503 en cours de flux, sortie encore en memoire -> rejeu propre."""
    class _FailOnceModels(_FakeModels):
        def __init__(self):
            super().__init__()
            self._first = True

        async def generate_content_stream(self, model, contents, config):
            self.stream_calls += 1
            self.last_config = config
            if self._first:
                self._first = False
                return _ChunkStream(
                    [_chunk('{"ok":')],
                    raise_after=1,
                    error=RuntimeError("429 RESOURCE_EXHAUSTED"),
                )
            return _ChunkStream([_chunk('{"ok": true}')])

    models = _FailOnceModels()
    provider = _gemini(models, monkeypatch, keys=("k1", "k2"))
    monkeypatch.setattr(provider, "note_rate_limit", lambda *a, **k: 0.0)

    result = asyncio.run(provider.generate_structured_output("prompt", _Answer))

    assert result.ok is True
    assert models.stream_calls == 2  # la cle suivante a bien ete essayee
    assert provider._first_byte_marked is True


def test_gemini_pas_de_rejeu_quand_le_contenu_a_ete_livre(monkeypatch):
    """Contenu deja livre -> echec franc. Le drapeau dit « livre », pas « vu »."""
    models = _FakeModels(
        chunks=[_chunk('{"ok":')],
        raise_after=1,
        error=RuntimeError("429 RESOURCE_EXHAUSTED"),
    )
    provider = _gemini(models, monkeypatch, keys=("k1", "k2"))
    provider._content_delivered = True

    with pytest.raises(ProviderServiceUnavailableError):
        asyncio.run(provider.generate_structured_output("prompt", _Answer))
    assert provider._first_byte_marked is True
    assert models.stream_calls == 1


def test_gemini_un_flux_vide_est_une_erreur_honnete(monkeypatch):
    models = _FakeModels(chunks=[])
    provider = _gemini(models, monkeypatch)

    with pytest.raises(Exception):
        asyncio.run(provider.generate_structured_output("prompt", _Answer))
    assert provider._first_byte_marked is False


def test_les_deux_providers_declarent_la_capacite():
    assert AnthropicProvider.STREAMS_STRUCTURED is True
    assert GeminiProvider.STREAMS_STRUCTURED is True

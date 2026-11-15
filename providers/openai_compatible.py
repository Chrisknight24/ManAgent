"""Flux SSE « OpenAI-compatible » : accumulation JSON + premier octet.

Module du **calque fournisseur**. Il ne connait que le format de flux le plus
repandu (lignes ``data: {json}`` terminees par ``[DONE]``) et rien d'autre :
aucun nom d'hote, aucun outil, aucune classe metier.

Deux responsabilites, separees pour etre testables sans reseau :

1. ``accumulate_json_stream`` : transforme un flux de lignes en texte complet
   et en usage, en signalant l'instant du **premier contenu** ;
2. ``stream_json_completion`` : meme boucle HTTP que les appels classiques
   (rotation de cles, 429/503, erreurs typ茅es), mais en lecture de flux.

Le contrat de sortie ne change pas : l'appelant recoit toujours le texte JSON
complet, exactement ce qu'il aurait recu sans streaming. Seule la latence
devient mesurable.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

import aiohttp

from providers.base_provider import (
    ProviderAuthError,
    ProviderError,
    ProviderQuotaExhaustedError,
    ProviderServiceUnavailableError,
)
from utils.logger import Logger


@dataclass
class StreamedJson:
    """Resultat d'un flux JSON accumule (jamais invente : champs absents = vides)."""

    text: str = ""
    usage: Optional[dict] = None
    first_content_at: Optional[float] = None
    chunks: int = 0
    done: bool = False
    malformed_lines: int = field(default=0)


def _line_text(raw: Any) -> str:
    """Une ligne de flux peut arriver en bytes ou en str : jamais d'exception."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _extract_content(payload: dict) -> str:
    """Texte d'un chunk (``choices[0].delta.content``), vide si absent."""
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        return content if isinstance(content, str) else ""
    except Exception:
        return ""


async def accumulate_json_stream(
    lines: AsyncIterator[Any],
    *,
    on_first_content: Optional[Callable[[], None]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> StreamedJson:
    """Accumule un flux SSE OpenAI-compatible.

    Garanties :
    - ``[DONE]`` termine proprement, sans erreur ;
    - une ligne illisible est **ignoree** (comportement historique du
      streaming) mais comptee, pour ne pas masquer un flux casse ;
    - un chunk ``{"error": ...}`` leve : une erreur serveur ne doit jamais
      devenir un JSON tronque silencieux ;
    - ``on_first_content`` est appele **une seule fois**, au premier caractere
      reellement recu (pas au premier octet de protocole).
    """
    outcome = StreamedJson()
    parts: list[str] = []
    async for raw in lines:
        line = _line_text(raw).strip()
        if not line or not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text:
            continue
        if payload_text == "[DONE]":
            outcome.done = True
            break
        try:
            payload = json.loads(payload_text)
        except Exception:
            outcome.malformed_lines += 1
            continue
        if not isinstance(payload, dict):
            outcome.malformed_lines += 1
            continue
        if payload.get("error"):
            raise ProviderError(f"Stream error: {payload.get('error')}")
        try:
            usage = payload.get("usage")
        except Exception:
            usage = None
        if isinstance(usage, dict):
            outcome.usage = usage
        content = _extract_content(payload)
        if not content:
            continue
        if outcome.first_content_at is None:
            outcome.first_content_at = clock()
            if on_first_content is not None:
                try:
                    on_first_content()
                except Exception:
                    pass
        outcome.chunks += 1
        parts.append(content)
    outcome.text = "".join(parts)
    return outcome


async def stream_json_completion(
    provider: Any,
    *,
    endpoint: str,
    payload: dict,
    label: str,
) -> StreamedJson:
    """POST en flux, avec la meme discipline de cles que l'appel classique.

    ``provider`` est duck-type : il doit exposer les helpers de
    ``BaseProvider`` (pool de cles, cooldowns, annulation). Aucun etat n'est
    duplique ici.
    """
    total_keys = max(1, len(getattr(provider, "api_keys_pool", []) or []))
    attempts = 0
    while attempts < total_keys:
        provider.check_cancelled()
        active_key = provider.get_active_api_key()
        if not active_key:
            raise ProviderQuotaExhaustedError(
                f"[{label}] Aucune cle API active ou disponible pour le flux."
            )
        headers = {
            "Authorization": f"Bearer {active_key}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    if response.status == 200:
                        outcome = await accumulate_json_stream(
                            response.content,
                            on_first_content=getattr(provider, "_mark_first_byte", None),
                        )
                        provider.promote_key(active_key)
                        try:
                            provider.note_provider_success()
                        except Exception:
                            pass
                        return outcome

                    error_text = await response.text()
                    masked = active_key[:8] if len(active_key) >= 8 else active_key
                    lowered = error_text.lower()
                    if (
                        response.status in (429, 503)
                        or "rate_limit" in lowered
                        or "quota" in lowered
                    ):
                        attempts += 1
                        wait = provider.note_rate_limit(
                            active_key, response.headers.get("Retry-After")
                        )
                        Logger.warning(
                            f"[{label} Stream Failover] Erreur {response.status} sur cle "
                            f"[{masked}...]. Rotation ({attempts}/{total_keys})."
                        )
                        if provider.has_available_keys():
                            await asyncio.sleep(wait)
                            continue
                        raise ProviderQuotaExhaustedError(
                            f"[{label}] Quota epuise sur toutes les cles : {error_text}"
                        )
                    if response.status in (401, 403):
                        provider.mark_key_in_cooldown(active_key, 3600.0)
                        raise ProviderAuthError(
                            f"[{label}] Cle invalide (401/403) : {error_text}"
                        )
                    raise ProviderError(
                        f"{label} API error {response.status}: {error_text}"
                    )
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as ce:
            attempts += 1
            provider.mark_key_in_cooldown(active_key, 30.0)
            if provider.has_available_keys():
                await asyncio.sleep(0.5)
                continue
            raise ProviderServiceUnavailableError(
                f"{label} stream network error: {ce}"
            ) from ce
    raise ProviderQuotaExhaustedError(
        f"[{label}] Quota epuise sur toutes les cles."
    )

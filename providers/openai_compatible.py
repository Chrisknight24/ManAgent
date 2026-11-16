"""Flux SSE « OpenAI-compatible » : accumulation JSON + premier octet.

Module du **calque fournisseur**. Il ne connait que le format de flux le plus
repandu (lignes ``data: {json}`` terminees par ``[DONE]``) et rien d'autre :
aucun nom d'hote, aucun outil, aucune classe metier.

La discipline d'appel (rotation de cles, 429/503, erreurs typees) vit dans
``providers.sse_transport`` : un seul endroit, tous formats confondus. Ici, on
ne garde que ce qui est propre au format : le texte, l'usage et le premier
contenu.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from providers.base_provider import ProviderError
from providers.sse_transport import stream_completion


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


def _bearer_headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _parse_json_stream(lines: Any, on_first_content=None) -> StreamedJson:
    return await accumulate_json_stream(lines, on_first_content=on_first_content)


async def stream_json_completion(
    provider: Any,
    *,
    endpoint: str,
    payload: dict,
    label: str,
) -> StreamedJson:
    """POST en flux OpenAI-compatible (discipline de cles partagee)."""
    return await stream_completion(
        provider,
        endpoint=endpoint,
        payload=payload,
        label=label,
        headers_for_key=_bearer_headers,
        parse=_parse_json_stream,
    )

"""Flux Anthropic : blocs de contenu, outil force, JSON progressif.

Anthropic ne renvoie pas du JSON en texte libre : la reponse structuree est un
appel d'outil force, dont les arguments arrivent par fragments
(``input_json_delta.partial_json``). On accumule ces fragments, puis on valide
le JSON complet — le contrat de sortie reste identique.

La discipline d'appel (rotation de cles, erreurs typees) est partagee via
``providers.sse_transport``. Ce module ne connait que le format Anthropic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from providers.base_provider import ProviderError
from providers.sse_transport import stream_completion


@dataclass
class StreamedToolUse:
    """Arguments d'outil accumules (jamais inventes : absents = vides)."""

    json_text: str = ""
    usage: Optional[dict] = None
    first_content_at: Optional[float] = None
    fragments: int = 0
    done: bool = False
    malformed_lines: int = 0
    stop_reason: Optional[str] = None
    tool_name: Optional[str] = None


def _merge_usage(target: Optional[dict], raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return target
    merged = dict(target or {})
    merged.update(raw)
    return merged


async def accumulate_tool_use_stream(
    lines: AsyncIterator[Any],
    *,
    expected_tool: Optional[str] = None,
    on_first_content: Optional[Callable[[], None]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> StreamedToolUse:
    """Accumule un flux Anthropic et rend les arguments JSON de l'outil."""
    outcome = StreamedToolUse()
    parts: list[str] = []
    async for raw in lines:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        line = text.strip()
        if not line or not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except Exception:
            outcome.malformed_lines += 1
            continue
        if not isinstance(payload, dict):
            outcome.malformed_lines += 1
            continue

        kind = payload.get("type")
        if kind == "error":
            raise ProviderError(f"Stream error: {payload.get('error')}")
        if kind == "message_start":
            message = payload.get("message") or {}
            outcome.usage = _merge_usage(outcome.usage, message.get("usage"))
            continue
        if kind == "content_block_start":
            block = payload.get("content_block") or {}
            if block.get("type") == "tool_use":
                outcome.tool_name = block.get("name") or outcome.tool_name
                if expected_tool and outcome.tool_name and outcome.tool_name != expected_tool:
                    raise ProviderError(
                        f"Anthropic a renvoye l'outil '{outcome.tool_name}' "
                        f"au lieu de '{expected_tool}'."
                    )
            continue
        if kind == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") != "input_json_delta":
                continue
            fragment = delta.get("partial_json")
            if not isinstance(fragment, str) or not fragment:
                continue
            if outcome.first_content_at is None:
                outcome.first_content_at = clock()
                if on_first_content is not None:
                    try:
                        on_first_content()
                    except Exception:
                        pass
            outcome.fragments += 1
            parts.append(fragment)
            continue
        if kind == "message_delta":
            outcome.usage = _merge_usage(outcome.usage, payload.get("usage"))
            delta = payload.get("delta") or {}
            if delta.get("stop_reason"):
                outcome.stop_reason = str(delta.get("stop_reason"))
            continue
        if kind == "message_stop":
            outcome.done = True
            break
        # `ping`, `content_block_stop` : bruit de protocole, rien a accumuler.
    outcome.json_text = "".join(parts)
    return outcome


def _anthropic_headers(key: str) -> dict:
    return {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


async def _parse_tool_use(
    lines: Any,
    on_first_content=None,
    *,
    expected_tool: Optional[str] = None,
) -> StreamedToolUse:
    return await accumulate_tool_use_stream(
        lines, expected_tool=expected_tool, on_first_content=on_first_content
    )


async def stream_tool_use_completion(
    provider: Any,
    *,
    endpoint: str,
    payload: dict,
    label: str,
    expected_tool: Optional[str] = None,
) -> StreamedToolUse:
    """POST en flux Anthropic, avec la discipline de cles partagee."""
    async def _parse(lines: Any, on_first_content=None) -> StreamedToolUse:
        return await _parse_tool_use(
            lines, on_first_content, expected_tool=expected_tool
        )

    return await stream_completion(
        provider,
        endpoint=endpoint,
        payload=payload,
        label=label,
        headers_for_key=_anthropic_headers,
        parse=_parse,
        retry_statuses=(429, 529, 503),
        retry_markers=("rate_limit", "quota", "overloaded"),
    )

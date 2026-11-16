"""Transport SSE commun : une seule discipline de cles pour tous les flux.

Le format des evenements differe d'un fournisseur a l'autre ; la **discipline
d'appel**, elle, ne doit pas differer : rotation de cles sur 429/503, erreurs
typees, annulation, et jamais de retry silencieux apres avoir livre du contenu.

Ce module ne connait aucun format : il recoit un `parse` (accumulateur) et un
`headers_for_key`. Les formats vivent dans `openai_compatible` (SSE JSON) et
`anthropic_stream` (blocs de contenu).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable, Optional

import aiohttp

from providers.base_provider import (
    ProviderAuthError,
    ProviderError,
    ProviderQuotaExhaustedError,
    ProviderServiceUnavailableError,
)
from utils.logger import Logger


# Un accumulateur recoit les lignes du flux et un hook « premier contenu ».
Parser = Callable[[Any, Optional[Callable[[], None]]], Awaitable[Any]]
HeadersFor = Callable[[str], dict]


async def stream_completion(
    provider: Any,
    *,
    endpoint: str,
    payload: dict,
    label: str,
    headers_for_key: HeadersFor,
    parse: Parser,
    retry_statuses: Iterable[int] = (429, 503),
    retry_markers: Iterable[str] = ("rate_limit", "quota"),
) -> Any:
    """POST en flux, avec la meme discipline que `_execute_http_request`.

    ``parse(response.content, on_first_content)`` doit retourner un objet
    portant un attribut ``usage`` (ou ``None``).
    """
    total_keys = max(1, len(getattr(provider, "api_keys_pool", []) or []))
    attempts = 0
    retry_statuses = tuple(retry_statuses)
    retry_markers = tuple(m.lower() for m in retry_markers)

    while attempts < total_keys:
        provider.check_cancelled()
        active_key = provider.get_active_api_key()
        if not active_key:
            raise ProviderQuotaExhaustedError(
                f"[{label}] Aucune cle API active ou disponible pour le flux."
            )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, headers=headers_for_key(active_key), json=payload
                ) as response:
                    if response.status == 200:
                        outcome = await parse(
                            response.content,
                            getattr(provider, "_mark_first_byte", None),
                        )
                        provider.promote_key(active_key)
                        return outcome

                    error_text = await response.text()
                    masked = active_key[:8] if len(active_key) >= 8 else active_key
                    lowered = error_text.lower()
                    if response.status in retry_statuses or any(
                        marker in lowered for marker in retry_markers
                    ):
                        attempts += 1
                        wait = provider.note_rate_limit(
                            active_key, response.headers.get("Retry-After")
                        )
                        Logger.warning(
                            f"[{label} Stream Failover] Erreur {response.status} sur cle "
                            f"[{masked}...]. Rotation ({attempts}/{total_keys})."
                        )
                        # Du contenu a deja ete livre : on ne rejoue pas en
                        # silence un appel partiellement consomme.
                        if getattr(provider, "_first_byte_marked", False):
                            raise ProviderServiceUnavailableError(
                                f"[{label}] Flux interrompu apres contenu : pas de rejeu silencieux."
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
            if getattr(provider, "_first_byte_marked", False) or not provider.has_available_keys():
                raise ProviderServiceUnavailableError(
                    f"{label} stream network error: {ce}"
                ) from ce
            await asyncio.sleep(0.5)
            continue
    raise ProviderQuotaExhaustedError(
        f"[{label}] Quota epuise sur toutes les cles."
    )

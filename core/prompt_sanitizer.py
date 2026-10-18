"""
Assainissement des sorties d'outils avant injection dans un prompt (texte).

But : empêcher les charges binaires (image_base64, data-URI, blobs base64) de
gonfler les prompts en texte. Le binaire reste disponible via Progressive
Disclosure (assets) ; le prompt ne reçoit qu'un marqueur court.
"""
import re
from typing import Any

_B64_KEY = re.compile(r"(base64|_b64|^b64$)", re.IGNORECASE)
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]+")
_B64_BLOB = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_PLACEHOLDER = "[binaire omis : {n} car.]"

_KEY_INLINE_LIMIT = 256
_BLOB_MIN_LENGTH = 1024


def _placeholder(length: int) -> str:
    return _PLACEHOLDER.format(n=length)


def redact_binary_payloads(value: Any) -> Any:
    """Retourne une copie où les charges binaires sont remplacées par un marqueur.

    Règles :
    - clé de dictionnaire évoquant du base64 + valeur str longue -> marqueur ;
    - chaîne data-URI base64 -> marqueur ;
    - blob base64 nu très long (> 1024) -> marqueur.
    Le reste est laissé intact (récursion sur dict/list).
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if (
                isinstance(key, str)
                and _B64_KEY.search(key)
                and isinstance(item, str)
                and len(item) > _KEY_INLINE_LIMIT
            ):
                out[key] = _placeholder(len(item))
            else:
                out[key] = redact_binary_payloads(item)
        return out
    if isinstance(value, list):
        return [redact_binary_payloads(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_binary_payloads(item) for item in value)
    if isinstance(value, str):
        sanitized = _DATA_URI.sub(lambda m: _placeholder(len(m.group(0))), value)
        if len(sanitized) >= _BLOB_MIN_LENGTH and _B64_BLOB.match(sanitized):
            return _placeholder(len(sanitized))
        return sanitized
    return value

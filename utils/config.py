"""
utils/config.py
===============
Résolution des références de secrets `"env:NOM"` dans les payloads.

Principe (12-factor) : aucun secret en clair dans les JSON, le git ou les
logs. `"api_key": "env:MANAGENT_GEMINI_KEY"` → valeur de la variable.
Marche aussi dans les listes (rotation multi-clés).
Variable absente → chaîne vide + avertissement (jamais de crash).
"""
import os
from typing import Any, Tuple

ENV_PREFIX = "env:"


def resolve_env_refs(obj: Any, _missing: list = None) -> Any:
    """Remplace récursivement les "env:NOM" par leur valeur d'environnement.

    Retourne l'objet résolu. `_missing` (optionnel) collecte les NOMS
    de variables introuvables.
    """
    if isinstance(obj, str) and obj.startswith(ENV_PREFIX):
        name = obj[len(ENV_PREFIX):].strip()
        val = os.environ.get(name)
        if val is None:
            if _missing is not None:
                _missing.append(name)
            return ""
        return val
    if isinstance(obj, dict):
        return {k: resolve_env_refs(v, _missing) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [resolve_env_refs(v, _missing) for v in obj]
    return obj


def resolve_payload_env(payload: dict) -> Tuple[dict, list]:
    """Résout un payload configure complet. Retour (payload, missing)."""
    missing: list = []
    return resolve_env_refs(payload, missing), missing

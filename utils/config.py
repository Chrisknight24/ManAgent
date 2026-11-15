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
from typing import Any, Optional, Tuple

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


VALID_HITL_POLICIES = ("strict", "balanced", "autonomous")


def resolve_hitl_policy(payload: dict) -> str:
    """Lit hitl_policy (racine prioritaire, sinon runtime_configuration).

    Valeur inconnue/absente -> "balanced". Fonction pure, testée.
    """
    if not isinstance(payload, dict):
        return "balanced"
    raw = payload.get("hitl_policy")
    if not raw:
        runtime_cfg = payload.get("runtime_configuration") or {}
        if isinstance(runtime_cfg, dict):
            raw = runtime_cfg.get("hitl_policy")
    val = str(raw or "").strip().lower()
    return val if val in VALID_HITL_POLICIES else "balanced"


VALID_ENVIRONMENTS = ("simulated", "production")

# Reglage du budget de lecture du monde. Meme forme que le manifeste hote
# (`world_snapshot.max_chars`), mais cote cerveau : c'est la DEMANDE, que le
# plafond hote peut reduire. Un operateur peut donc regler sans redeployer.
WORLD_SNAPSHOT_KEY = "world_snapshot"


def resolve_world_snapshot_chars(payload: dict) -> Optional[int]:
    """Lit world_snapshot.max_chars (racine prioritaire, sinon runtime_configuration).

    Retourne un entier > 0, ou None si rien n'est declare ou si la valeur est
    illisible (le defaut du cerveau s'applique alors). Fonction pure.
    """
    if not isinstance(payload, dict):
        return None
    holder = payload.get(WORLD_SNAPSHOT_KEY)
    if not isinstance(holder, dict):
        runtime_cfg = payload.get("runtime_configuration") or {}
        if isinstance(runtime_cfg, dict):
            holder = runtime_cfg.get(WORLD_SNAPSHOT_KEY)
    if not isinstance(holder, dict):
        return None
    value = holder.get("max_chars")
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

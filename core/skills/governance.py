"""
core/skills/governance.py
=========================
Gouvernance des skills pilotée par l'hôte (host-centered), valeurs par défaut
affichées au contrat (docs/HOST_CONTRACT.md §7c).

Ordre d'application (le plus précis gagne) :
1. défauts ManAgent ("defaut")
2. réglage global hôte `skill_governance` ("hôte")
3. sensibilité des outils `sensitivity: high|low` ("sensibilité")
4. réglage par skill `skill_governance.skills.{id}` ("skill")

Fonctions pures, sans LLM, testées vite. L'émission d'events se fait côté
appelant (Solver), jamais ici.
"""

from typing import Dict, List, Optional, Tuple, Any

from core.constants import SKILL_GOVERNANCE_DEFAULTS

INT_PARAMS = (
    "discovery_threshold",
    "shadow_success_threshold",
    "shadow_mismatch_threshold",
    "circuit_breaker_max_failures",
)

SOURCE_DEFAUT = "defaut"
SOURCE_HOTE = "hôte"
SOURCE_SENSIBILITE = "sensibilité"
SOURCE_SKILL = "skill"


def normalize_tier(tier: Any) -> str:
    """strict (serré) | standard | relaxed (souple). Inconnu/legacy → standard.

    Note : le legacy 'low' (vieux risk_level) vaut standard pour ne rien
    changer aux skills existants. La souplesse vient de sensitivity 'low'
    (voir normalize_sensitivity), pas du vieux mot.
    """
    t = str(tier or "standard").strip().lower()
    if t in ("strict", "critical", "high", "serre", "serré"):
        return "strict"
    if t in ("relaxed", "souple", "relache", "relâché"):
        return "relaxed"
    return "standard"


def normalize_sensitivity(value: Any) -> str:
    """high | normal | low. Inconnu → normal."""
    v = str(value or "normal").strip().lower()
    if v in ("high", "strict", "critical", "haute", "haut"):
        return "high"
    if v in ("low", "relaxed", "souple", "basse", "bas"):
        return "low"
    return "normal"


def tier_for_tools(tool_names: List[str], sensitivity_map: Dict[str, str]) -> str:
    """Niveau d'un skill depuis la sensibilité max de ses outils."""
    sens = [normalize_sensitivity(sensitivity_map.get(t)) for t in tool_names or []]
    if any(s == "high" for s in sens):
        return "strict"
    if any(s == "low" for s in sens):
        return "relaxed"
    return "standard"


def tool_sensitivity_map(tools: Any) -> Dict[str, str]:
    """{nom_outil: high|normal|low} depuis les dicts d'outils du manifeste."""
    out: Dict[str, str] = {}
    for t in tools or []:
        if isinstance(t, dict) and t.get("name"):
            out[str(t["name"])] = normalize_sensitivity(t.get("sensitivity"))
    return out


def tool_requires_env_map(tools: Any) -> Dict[str, List[str]]:
    """{nom_outil: [clés d'env requises]} depuis `requires_env` du manifeste."""
    out: Dict[str, List[str]] = {}
    for t in tools or []:
        if isinstance(t, dict) and t.get("name"):
            req = t.get("requires_env") or []
            if isinstance(req, str):
                req = [req]
            req = [str(k).strip() for k in req if str(k).strip()]
            if req:
                out[str(t["name"])] = req
    return out


def canonical_skill_id(action: str, obj: str) -> Optional[str]:
    """ID neutre agnostique (P3). L'ancien préfixe `desktop.` reste lu en repli."""
    a = str(action or "").strip().replace(" ", "_")
    o = str(obj or "").strip().replace(" ", "_")
    if not a or not o:
        return None
    return f"skill.{a}.{o}"


def legacy_skill_id(action: str, obj: str) -> Optional[str]:
    """Ancien préfixe, lecture seule (migration P3)."""
    a = str(action or "").strip().replace(" ", "_")
    o = str(obj or "").strip().replace(" ", "_")
    if not a or not o:
        return None
    return f"desktop.{a}.{o}"


def _apply_tier(values: Dict[str, Any], tier: str) -> Dict[str, Any]:
    """Multiplicateurs de sensibilité sur les DÉFAUTS (jamais sur choix hôte)."""
    if tier == "standard":
        return values
    out = dict(values)
    if tier == "strict":
        for p in INT_PARAMS:
            out[p] = max(1, int(out[p]) // 2)
        mr = out.get("max_repairs")
        out["max_repairs"] = None if mr is None else max(1, int(mr) // 2)
        try:
            out["champion_margin"] = min(1.0, float(out.get("champion_margin", 0.05)) * 2)
        except Exception:
            pass
    elif tier == "relaxed":
        for p in INT_PARAMS:
            out[p] = int(out[p]) * 2
        mr = out.get("max_repairs")
        out["max_repairs"] = None if mr is None else int(mr) * 2
        try:
            out["champion_margin"] = max(0.0, float(out.get("champion_margin", 0.05)) / 2)
        except Exception:
            pass
    return out


def resolve_skill_governance(
    skill_id: Optional[str] = None,
    tier: Any = "standard",
    host_governance: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Retourne (valeurs, sources). Sources : defaut | hôte | sensibilité | skill.

    max_repairs None = infini (choix hôte assumé). Entiers clampés ≥ 1.
    """
    tier = normalize_tier(tier)
    host = dict(host_governance or {})
    per_skill = host.get("skills") or {}
    skill_over = per_skill.get(skill_id) if skill_id else None
    skill_over = dict(skill_over) if isinstance(skill_over, dict) else {}

    values: Dict[str, Any] = dict(SKILL_GOVERNANCE_DEFAULTS)
    sources: Dict[str, str] = {k: SOURCE_DEFAUT for k in values}

    # 1. Global hôte (None explicite sur max_repairs = infini assumé).
    for k in list(values.keys()):
        if k in host and host[k] is not None:
            values[k] = host[k]
            sources[k] = SOURCE_HOTE
    if "max_repairs" in host and host["max_repairs"] is None:
        values["max_repairs"] = None
        sources["max_repairs"] = SOURCE_HOTE

    # 2. Sensibilité (seulement sur les valeurs encore par défaut ;
    # un infini explicite de l'hôte reste infini).
    tiered = _apply_tier(
        {k: v for k, v in SKILL_GOVERNANCE_DEFAULTS.items()}, tier
    )
    if tier != "standard":
        for k in list(values.keys()):
            if sources[k] == SOURCE_DEFAUT:
                values[k] = tiered[k]
                sources[k] = SOURCE_SENSIBILITE

    # 3. Par skill (gagne toujours, None explicite = infini assumé).
    for k in list(values.keys()):
        if k in skill_over and skill_over[k] is not None:
            values[k] = skill_over[k]
            sources[k] = SOURCE_SKILL
    if "max_repairs" in skill_over and skill_over["max_repairs"] is None:
        values["max_repairs"] = None
        sources["max_repairs"] = SOURCE_SKILL

    # Assainissement : entiers ≥ 1, marge 0..1, max_repairs None ou ≥ 0.
    for p in INT_PARAMS:
        try:
            values[p] = max(1, int(values[p]))
        except Exception:
            values[p] = int(SKILL_GOVERNANCE_DEFAULTS[p])
    try:
        values["champion_margin"] = max(0.0, min(1.0, float(values["champion_margin"])))
    except Exception:
        values["champion_margin"] = float(SKILL_GOVERNANCE_DEFAULTS["champion_margin"])
    mr = values.get("max_repairs")
    if mr is not None:
        try:
            values["max_repairs"] = max(0, int(mr))
        except Exception:
            values["max_repairs"] = int(SKILL_GOVERNANCE_DEFAULTS["max_repairs"])

    return values, sources

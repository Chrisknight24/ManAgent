"""
core/alignment.py
=================
Capteur-vs-juge : mesurer l'incertitude d'un plan, inciter sans sanctionner.

Fonctions pures (aucun LLM, testables en millisecondes). Voir `docs/ALIGNMENT.md`.
Jamais de nom d'hôte, d'outil ou d'application en dur : tout vient du
manifeste (champ outil optionnel `effects`) et des listes passées en args.
"""

from typing import Dict, List, Set, Tuple, Any, Optional
from core.i18n import _


EFFECT_DETERMINISTIC = "deterministic"
EFFECT_UNCERTAIN = "uncertain"


def effect_of(tool: Dict[str, Any]) -> str:
    """Effet effectif d'un outil déclaré : deterministic | uncertain | none.

    `none` = pas d'incertitude (perception, utilitaire : lire ne change rien).
    Explicite > défaut prudent : `[action]` sans déclaration = uncertain.
    Fonction pure.
    """
    if not isinstance(tool, dict):
        return "none"
    declared = str(tool.get("effects") or "").strip().lower()
    if declared in (EFFECT_DETERMINISTIC, EFFECT_UNCERTAIN):
        if declared == EFFECT_DETERMINISTIC:
            return EFFECT_DETERMINISTIC
        return EFFECT_UNCERTAIN
    kind = str(tool.get("kind") or "action").strip().lower()
    if kind in ("perception", "utility"):
        return "none"
    return EFFECT_UNCERTAIN


def uncertain_names(tools: Any) -> Set[str]:
    """Noms des outils externes d'action à effet incertain. Fonction pure."""
    names: Set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        if str(tool.get("source") or "external") != "external":
            continue
        if effect_of(tool) == EFFECT_UNCERTAIN:
            names.add(name)
    return names


def toolset_nudge(tools: Any) -> str:
    """Nudge général solver : part d'actions incertaines parmi les outils dispos.

    Vide = rien à signaler (tout déterministe ou aucune action).
    Incitatif seulement, jamais bloquant.
    """
    actions = [
        t for t in (tools or [])
        if isinstance(t, dict) and str(t.get("kind") or "action").lower() not in ("perception", "utility")
    ]
    if not actions:
        return ""
    uncertain = sum(1 for t in actions if effect_of(t) == EFFECT_UNCERTAIN)
    if uncertain == 0:
        return ""
    share = uncertain / max(1, len(actions))
    if share >= 0.5:
        return _(
            "Environnement plutôt incertain ({n}/{total} actions à effet incertain) : "
            "privilégie une stratégie avec points de contrôle "
            "(lire avant de décider, vérifier après avoir agi)."
        ).format(n=uncertain, total=len(actions))
    return _(
        "Environnement plutôt stable ({n}/{total} actions à effet incertain) : "
        "une vérification légère suffit, sans alourdir la stratégie."
    ).format(n=uncertain, total=len(actions))


def plan_risk(tool_names_in_order: List[str], uncertain: Set[str], perception: Set[str]) -> Dict[str, Any]:
    """Risque d'un plan : actions incertaines non couvertes par une lecture.

    `tool_names_in_order` = noms des steps `tool_call` dans l'ordre (le reste
    est ignoré par l'appelant : direct_answer, sous-tâches gérées en récursif).
    Est une lecture : `perceive_understand` ou outil `[perception]`.
    Fonction pure. Voir `docs/ALIGNMENT.md` §4.
    """
    seq = [str(t or "").strip() for t in (tool_names_in_order or [])]
    seq = [t for t in seq if t]
    uncertain_set = set(uncertain or set())
    perception_set = set(perception or set())
    last_read = -1
    for idx, name in enumerate(seq):
        if name == "perceive_understand" or name in perception_set:
            last_read = idx
    uncovered = sum(
        1 for idx, name in enumerate(seq)
        if name in uncertain_set and idx > last_read
    )
    total_uncertain = sum(1 for name in seq if name in uncertain_set)
    level = "low" if uncovered == 0 else "high"
    return {
        "total_actions": len(seq),
        "total_uncertain": total_uncertain,
        "uncovered": uncovered,
        "last_perception_index": last_read,
        "level": level,
    }


def risk_sentence(risk: Dict[str, Any]) -> str:
    """Phrase-fait pour prompt (convergence finale). Vide si risque bas."""
    if not isinstance(risk, dict) or risk.get("level") != "high":
        return ""
    return _(
        "Fait alignement : {n} action(s) incertaine(s) depuis la dernière "
        "lecture du monde. Une vérification par perception peut être "
        "nécessaire pour confirmer l'alignement."
    ).format(n=risk.get("uncovered", 0))


def planner_nudge(tool_names_in_order: List[str], uncertain: Set[str], perception: Set[str]) -> str:
    """Nudge concret planner : nomme les outils incertains utilisés. Crédit au planner."""
    seq = [str(t or "").strip() for t in (tool_names_in_order or [])]
    used = sorted({t for t in seq if t in (uncertain or set())})
    if not used:
        return ""
    risk = plan_risk(seq, uncertain, perception)
    if risk["level"] == "low":
        return ""
    return _(
        "Tu utilises des outils à effet incertain ({names}) sans lecture "
        "du monde derrière : une vérification finale par perception sera "
        "probablement requise. À toi de l'écrire ou d'assumer le risque."
    ).format(names=", ".join(used[:5]))


def allowed_values(tool: Dict[str, Any], param: str) -> Optional[List[str]]:
    """Valeurs autorisées déclarées au schéma (`enum`), ou None.

    Agnostique : on lit le schéma de l'hôte, jamais de liste en dur.
    Fonction pure.
    """
    try:
        if not isinstance(tool, dict):
            return None
        params = tool.get("parameters") or {}
        props = params.get("properties") or {}
        entry = props.get(param) or {}
        enum = entry.get("enum") if isinstance(entry, dict) else None
        if isinstance(enum, list) and enum:
            return [str(v) for v in enum]
        return None
    except Exception:
        return None


def invalid_enum_value(tool: Dict[str, Any], args: Any) -> Optional[str]:
    """Valeur hors `enum` déclaré : message honnête, sinon None.

    Ne juge que les valeurs texte présentes. Param absent ou schéma
    sans `enum` = rien à dire (autres contrôles s'en chargent).
    Fonction pure.
    """
    try:
        if not isinstance(args, dict):
            return None
        for key, val in args.items():
            if not isinstance(val, str):
                continue
            allowed = allowed_values(tool, str(key))
            if allowed is not None and val not in allowed:
                return _("param '{param}' = '{val}' non permis (attendus : {allowed})").format(
                    param=key, val=val, allowed="|".join(allowed))
        return None
    except Exception:
        return None

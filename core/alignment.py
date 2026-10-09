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
import re


EFFECT_DETERMINISTIC = "deterministic"
EFFECT_UNCERTAIN = "uncertain"

# Convention de contrat (docs/HOST_CONTRACT.md) : l'hôte qui veut
# fournir son état du monde expose un outil avec CE nom, sans argument
# obligatoire. Nom conventionnel documenté (comme les noms d'actions
# du protocole), jamais deviné : un seul endroit, pas de fallback créatif.
WORLD_STATE_TOOL = "get_world_state"


async def fetch_world_state_snapshot(runtime_state, max_chars: int = 800) -> str:
    """Photo d'état du monde : manifeste déclaré > convention > rien.

    Retourne du texte brut capé, ou "" avec un log `snapshot.skip`
    motivé (no_world_state_tool, ...). Jamais d'exception, jamais de
    devinette sur un outil au hasard. Fail-open total.
    """
    from utils.logger import Logger
    try:
        manifest = getattr(runtime_state, "host_manifest", None)
        cfg = {}
        if manifest is not None:
            for holder in (getattr(manifest, "metadata", None), getattr(manifest, "environment", None)):
                if isinstance(holder, dict) and isinstance(holder.get("world_snapshot"), dict):
                    cfg = holder["world_snapshot"]
                    break
        source_tool = str((cfg.get("source_tool") or "")).strip()
        source_args = cfg.get("source_args") or {}
        if not isinstance(source_args, dict):
            source_args = {}
        try:
            limit = int(cfg.get("max_chars") or max_chars)
        except Exception:
            limit = max_chars
        limit = max(1, min(4000, limit))
        if not source_tool:
            tm = getattr(runtime_state, "tools_manager", None)
            known = set(tm.known_tool_names()) if tm is not None and hasattr(tm, "known_tool_names") else set()
            if WORLD_STATE_TOOL in known:
                source_tool = WORLD_STATE_TOOL
                source_args = {}
        if not source_tool:
            Logger.info("[snapshot] snapshot.skip reason=no_world_state_tool.")
            return ""
        tm = getattr(runtime_state, "tools_manager", None)
        if tm is None or not hasattr(tm, "execute_tool"):
            Logger.info("[snapshot] snapshot.skip reason=no_tools_manager.")
            return ""
        result_str = await tm.execute_tool(source_tool, dict(source_args))
        import json as _json
        try:
            parsed = _json.loads(result_str) if isinstance(result_str, str) else result_str
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            if not parsed.get("result", True):
                Logger.info("[snapshot] snapshot.skip reason=source_failed.")
                return ""
            data = parsed.get("data", parsed)
        else:
            data = parsed
        if data is None:
            return ""
        text = data if isinstance(data, str) else _json.dumps(data, ensure_ascii=False)
        text = str(text).strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        return text
    except Exception as e:
        try:
            Logger.debug(f"[snapshot] snapshot.skip reason=error ({e}).")
        except Exception:
            pass
        return ""


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


def world_guidance_visible(tools: Any) -> bool:
    """Faut-il afficher le guidage perception ? Masqué SEULEMENT si l'hôte
    déclare tout déterministe ET sans aucun outil `[perception]`.

    Défaut prudent : visible (non déclaré = incertain). Fonction pure.
    """
    try:
        externals = [
            t for t in (tools or [])
            if isinstance(t, dict) and str(t.get("source") or "external") == "external"
        ]
        if not externals:
            return True
        if any(str(t.get("kind") or "action").lower() == "perception" for t in externals):
            return True
        actions = [
            t for t in externals
            if str(t.get("kind") or "action").lower() not in ("perception", "utility")
        ]
        if not actions:
            return True
        return any(effect_of(t) == EFFECT_UNCERTAIN for t in actions)
    except Exception:
        return True


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


def _goal_tokens(text: str) -> Set[str]:
    tokens = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split()
    return set(w for w in tokens if len(w) > 3)


def goal_similarity(a: str, b: str) -> float:
    """Similarité token (Jaccard) entre deux buts. Fonction pure."""
    sa, sb = _goal_tokens(a), _goal_tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def redelegation_clusters(goals: List[str], threshold: float = 0.60) -> List[List[int]]:
    """Grappes de buts quasi identiques (chaînes de re-délégation).

    `goals` = buts des solvers/plans dans l'ordre. Retourne les groupes
    d'indices dont la similarité deux-à-deux atteint le seuil.
    Zéro grappe = aucune boucle même-but. Fonction pure, testable en ms.
    Sert de test de succès de vague sur logs réels.
    """
    items = [str(g or "") for g in (goals or [])]
    clusters: List[List[int]] = []
    for idx in range(len(items)):
        placed = False
        for group in clusters:
            if any(goal_similarity(items[idx], items[member]) >= threshold for member in group):
                group.append(idx)
                placed = True
                break
        if not placed:
            clusters.append([idx])
    return [sorted(group) for group in clusters if len(group) > 1]

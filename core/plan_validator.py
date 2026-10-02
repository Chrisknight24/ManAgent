"""
core/plan_validator.py
=======================
Validation finale d'un plan proposé par un Solver, avant exécution
("LLM Judge" évoqué en commentaire dans Orchestrator.validate_plan).

Volontairement extrait d'Orchestrator dans sa propre classe — comme
Retriever/SignatureExtractor/MissionCompactor le sont déjà pour Solver —
pour rester testable sans avoir à instancier tout l'Orchestrateur (session
store, mission store, embeddings, event bus...). Orchestrator.validate_plan
construit un PlanValidator avec ce dont il a besoin et lui délègue le
jugement.

Trois responsabilités, dans cet ordre :
1. Détection de motifs récursifs (code déterministe, pas de LLM) : le plan
   proposé a-t-il EXACTEMENT la même structure qu'une tentative précédente
   déjà en échec, pour ce même Solver ?
2. Jugement de conformité par LLM structuré (PlanValidationDecision), qui
   reçoit le signal du point 1 comme un FAIT injecté dans le prompt plutôt
   que de devoir le redécouvrir lui-même à chaque appel.
3. Si le plan est conforme mais jugé nécessiter une confirmation humaine :
   délégation à un callback fourni par l'appelant (Orchestrator branchera
   ça sur le mécanisme Future/call_id déjà utilisé pour les outils externes).
   Sans canal de confirmation disponible, on refuse PAR PRUDENCE — jamais de
   passage silencieux.
"""

from __future__ import annotations

import re
from typing import List, Optional, Callable, Awaitable, Any, Dict, Set, Tuple
from core.plan_models import Plan, PlanValidationDecision, RiskLevel, DepthEscalationDecision, StepType
from core.execution_models import PlanAttempt
from core.i18n import _
from utils.logger import Logger


def _normalize_text(text: str) -> str:
    """Normalise un texte pour comparaison sémantique simple (minuscules, sans ponctuation)."""
    if not text:
        return ""
    t = text.lower().strip()
    t = re.sub(r'[\W_]+', ' ', t).strip()
    return t


def _parse_step_args(raw: Any) -> Dict[str, Any]:
    """Arguments d'étape en dict (jamais d'exception, jamais de secret filtré ici)."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "{}":
        return {}
    try:
        import json as _json
        parsed = _json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _short_value(value: Any, limit: int = 40) -> str:
    """Valeur tronquée pour affichage (anti-fuite de longs secrets/texte)."""
    try:
        text = value if isinstance(value, str) else __import__("json").dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def compare_plan_novelty(plan: Any, previous_attempts: Optional[List[Any]] = None,
                         max_shown: int = 3) -> Optional[str]:
    """Dit SI le plan est nouveau, et OÙ (structure ? arguments ? textes ?).

    Le juge LLM ne voit que des noms d'étapes : sans ce calcul, un plan qui
    corrige juste un argument (ex : touche `lwin` → `WIN`) ressemble à une
    redite et se fait refuser à tort. Fonction pure, sans LLM, testée vite.
    Retourne un bloc texte injecté dans le prompt, ou None (premier plan).
    """
    failed = [a for a in (previous_attempts or [])
              if getattr(a, "outcome", None) == "failed"
              and getattr(a, "proposed_plan", None)]
    if not failed:
        return None

    current_steps = list(getattr(plan, "steps", []) or [])
    current_sig = PlanValidator._plan_step_signature(current_steps)
    current_args = [_parse_step_args(getattr(s, "tool_args_json", "{}")) for s in current_steps]
    current_descs = [_normalize_text(getattr(s, "description", "")) for s in current_steps]

    blocks: List[str] = []
    for attempt in failed[-max_shown:]:
        proposed = getattr(attempt, "proposed_plan", None) or {}
        past_steps = proposed.get("steps", []) if isinstance(proposed, dict) else []
        past_sig = PlanValidator._plan_step_signature(past_steps)
        att_num = getattr(attempt, "attempt_number", "?")

        if past_sig != current_sig:
            blocks.append(
                _("Tentative {n} : STRUCTURE NOUVELLE (outils/types différents) — jugez sur le fond, pas de redite.")
                .format(n=att_num)
            )
            continue

        changes: List[str] = []
        for idx, past in enumerate(past_steps):
            past_id = past.get("id", f"step_{idx + 1}") if isinstance(past, dict) else f"step_{idx + 1}"
            past_args = _parse_step_args(past.get("tool_args_json") if isinstance(past, dict) else {})
            cur_args = current_args[idx] if idx < len(current_args) else {}
            for key in sorted(set(past_args) | set(cur_args)):
                old, new = past_args.get(key), cur_args.get(key)
                if old != new:
                    if key not in past_args:
                        changes.append(f"{past_id}: +{key}={_short_value(new)}")
                    elif key not in cur_args:
                        changes.append(f"{past_id}: -{key} (valait {_short_value(old)})")
                    else:
                        changes.append(f"{past_id}: {key} {_short_value(old)}→{_short_value(new)}")
            past_desc = _normalize_text(past.get("description", "") if isinstance(past, dict) else "")
            if idx < len(current_descs) and past_desc != current_descs[idx]:
                changes.append(_("{sid} : texte d'étape reformulé").format(sid=past_id))

        if not changes:
            blocks.append(
                _("Tentative {n} : IDENTIQUE (même structure, mêmes arguments, mêmes textes) — vigilance maximale, refusez si la cause est ignorée.")
                .format(n=att_num)
            )
        else:
            shown = "; ".join(changes[:6])
            blocks.append(
                _("Tentative {n} : MÊME STRUCTURE mais ARGUMENTS MODIFIÉS ({changes}) — ceci PEUT répondre à la cause. Validez si ça la traite, refusez seulement si ça l'ignore.")
                .format(n=att_num, changes=shown)
            )

    return "\n".join(blocks) if blocks else None


def find_unknown_plan_tools(plan: Any, known_tool_names=None,
                            production_skill_ids=None) -> List[str]:
    """Gate déterministe (fail-fast) : outils/skills du plan qui N'EXISTENT PAS.

    - Étapes `tool_call` avec `tool_name` hors `known_tool_names` → "tool:<nom>".
    - `execute_skill` avec `skill_id` hors `production_skill_ids` → "skill:<id>".
    - Si les référentiels sont None → pas de vérification (rétro-compat).
    Fonction pure, sans LLM, testable vite.
    """
    if known_tool_names is None and production_skill_ids is None:
        return []
    known = set(known_tool_names or [])
    prod = set(production_skill_ids or [])
    unknown: List[str] = []
    for step in getattr(plan, "steps", []) or []:
        stype = getattr(getattr(step, "type", None), "value", getattr(step, "type", None))
        if stype != "tool_call":
            continue
        tname = (getattr(step, "tool_name", None) or "").strip()
        if not tname or tname not in known:
            unknown.append(f"tool:{tname or '?'}")
            continue
        if tname == "execute_skill" and production_skill_ids is not None:
            sid = ""
            try:
                import json as _json
                args = _json.loads(getattr(step, "tool_args_json", "{}") or "{}")
                sid = str(args.get("skill_id", "") or "").strip()
            except Exception:
                sid = ""
            if not sid or sid not in prod:
                unknown.append(f"skill:{sid or '?'}")
    return unknown


def find_malformed_step_args(plan: Any) -> List[str]:
    """Gate déterministe (fail-fast) : `tool_args_json` qui ne parse pas.

    Retourne les ids d'étapes fautives. Un JSON d'args malformé (clés
    dupliquées, traîne) tuait l'executor avant tout appel hôte (Mission-1).
    Fonction pure, sans LLM, testée vite.
    """
    import json as _json

    bad: List[str] = []
    for step in getattr(plan, "steps", []) or []:
        raw = getattr(step, "tool_args_json", None)
        if raw is None or (isinstance(raw, str) and raw.strip() in ("", "{}")):
            continue
        if isinstance(raw, dict):
            continue
        try:
            _json.loads(raw)
        except Exception:
            bad.append(str(getattr(step, "id", "?")))
    return bad


# Outils réservés au validateur/superviseur : le planner ne doit jamais
# les émettre en tool_call (ils contourneraient la politique HITL).
# `force_user_options` RESTE un outil normal (décision Christian).
RESERVED_PLANNER_TOOLS = frozenset({"human_validation"})


def find_reserved_plan_tools(plan: Any) -> List[str]:
    """Étapes tool_call utilisant un outil réservé. Gate déterministe."""
    bad: List[str] = []
    for step in getattr(plan, "steps", []) or []:
        stype = getattr(getattr(step, "type", None), "value", getattr(step, "type", None))
        if stype != "tool_call":
            continue
        tname = (getattr(step, "tool_name", None) or "").strip()
        if tname in RESERVED_PLANNER_TOOLS:
            bad.append(str(getattr(step, "id", "?")))
    return bad


def find_skill_missing_params(plan: Any, skill_required_params: Optional[Dict[str, List[str]]] = None) -> Dict[str, List[str]]:
    """Étapes execute_skill dont des paramètres requis manquent (avertissement, jamais rejet).

    skill_required_params : {skill_id: [noms requis]}. Retourne {step_id: [noms manquants]}.
    Manquant = absent, vide, ou placeholder non résolu (@$_param_... / $@_...).
    Sans référentiel (None) : rien à dire (rétro-compat). Fonction pure, testée vite.
    """
    if not skill_required_params:
        return {}
    missing: Dict[str, List[str]] = {}
    for step in getattr(plan, "steps", []) or []:
        stype = getattr(getattr(step, "type", None), "value", getattr(step, "type", None))
        if stype != "tool_call":
            continue
        if (getattr(step, "tool_name", None) or "").strip() != "execute_skill":
            continue
        try:
            import json as _json
            args = _json.loads(getattr(step, "tool_args_json", "{}") or "{}")
        except Exception:
            continue
        if not isinstance(args, dict):
            continue
        sid = str((args.get("skill_id", "") or "")).strip()
        required = skill_required_params.get(sid) or []
        if not required:
            continue
        params = args.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        lacking = []
        for name in required:
            val = params.get(name)
            if val is None or (isinstance(val, str) and not val.strip()):
                lacking.append(name)
            elif isinstance(val, str) and ("@$_param_" in val or "$@_" in val):
                lacking.append(name)
        if lacking:
            missing[str(getattr(step, "id", "?"))] = lacking
    return missing


def _tool_declares_media(tool_name: str, tool_returns: Any) -> bool:
    """L'outil déclare-t-il des charges médias (le validateur le sait) ?"""
    if not tool_returns or not isinstance(tool_returns, dict):
        return False
    declared = tool_returns.get(tool_name) or []
    if not isinstance(declared, list):
        return False
    from core.discovery.data_asset import is_media_mime
    return any(
        isinstance(d, dict) and is_media_mime(str(d.get("asset") or ""))
        for d in declared
    )


def find_direct_perception_calls(plan: Any, perception_tool_names=None, tool_returns=None) -> List[str]:
    """Étapes tool_call directes vers un outil de perception externe.

    La lecture du monde passe UNIQUEMENT par `perceive_understand`
    (le LLM ne tient pas le pattern tout seul). Gate déterministe.
    Sans référentiel (None) : pas de vérification (rétro-compat).
    Outils à charges médias déclarées exclus : leur appel direct préserve
    les pixels (normalisés en assets), la réécriture les tuerait.
    """
    if not perception_tool_names:
        return []
    targets = set(perception_tool_names)
    bad: List[str] = []
    for step in getattr(plan, "steps", []) or []:
        stype = getattr(getattr(step, "type", None), "value", getattr(step, "type", None))
        if stype != "tool_call":
            continue
        tname = (getattr(step, "tool_name", None) or "").strip()
        if tname in targets and not _tool_declares_media(tname, tool_returns):
            bad.append(str(getattr(step, "id", "?")))
    return bad


def repair_direct_perception_calls(plan: Any, perception_tool_names=None, tool_returns=None) -> list:
    """Réécrit EN PLACE les appels perception directs en `perceive_understand`.

    Au lieu de refuser en boucle un planner qui ne sait pas se corriger :
    question = description de l'étape, source = outil + args d'origine.
    Outils à charges médias déclarées exclus (pixels préservés).
    Retourne les ids réparés. Pur hormis la mutation du plan (pratique du
    code : les plans sont déjà mutés ailleurs).
    """
    import json as _json

    if not perception_tool_names:
        return []
    targets = set(perception_tool_names)
    repaired: List[str] = []
    for step in getattr(plan, "steps", []) or []:
        stype = getattr(getattr(step, "type", None), "value", getattr(step, "type", None))
        if stype != "tool_call":
            continue
        tname = (getattr(step, "tool_name", None) or "").strip()
        if tname not in targets or _tool_declares_media(tname, tool_returns):
            continue
        try:
            src_args = _json.loads(getattr(step, "tool_args_json", "{}") or "{}")
            if not isinstance(src_args, dict):
                src_args = {}
        except Exception:
            src_args = {}
        step.tool_name = "perceive_understand"
        step.tool_args_json = _json.dumps({
            "question": getattr(step, "description", "") or "",
            "source_tool": tname,
            "source_args": src_args,
            "format_response": "",
        }, ensure_ascii=False)
        repaired.append(str(getattr(step, "id", "?")))
    return repaired


class PlanValidationOutcome:
    """
    Résultat riche de la validation. Remplace le simple bool historique de
    Orchestrator.validate_plan (qui perdait toute justification), tout en
    restant utilisable comme un bool via __bool__ pour les appelants qui ne
    veulent que la décision brute.
    """

    def __init__(
        self,
        is_valid: bool,
        reason: str,
        risk_level: RiskLevel = RiskLevel.LOW,
        requires_human_confirmation: bool = False,
        human_confirmed: Optional[bool] = None,
        irreversibility_flags: Optional[List[str]] = None,
    ):
        self.is_valid = is_valid
        self.reason = reason
        self.risk_level = risk_level
        self.requires_human_confirmation = requires_human_confirmation
        self.human_confirmed = human_confirmed
        self.irreversibility_flags = irreversibility_flags or []

    def __bool__(self) -> bool:
        return self.is_valid

    def __repr__(self) -> str:
        return (
            f"PlanValidationOutcome(is_valid={self.is_valid}, risk_level={self.risk_level!r}, "
            f"requires_human_confirmation={self.requires_human_confirmation}, "
            f"human_confirmed={self.human_confirmed}, reason={self.reason!r})"
        )


class PlanValidator:
    def __init__(
        self,
        llm: Any,
        prompt_loader: Any,
        rules_text: str,
        language: str = "fr",
        request_human_confirmation: Optional[
            Callable[[Plan, PlanValidationDecision], Awaitable[bool]]
        ] = None,
        hitl_policy: str = "balanced",
        human_validation_history: Optional[List[Dict[str, Any]]] = None,
        available_tools: Optional[Set[str]] = None,
        production_skills: Optional[Set[str]] = None,
        perception_tools: Optional[Set[str]] = None,
        tool_returns: Optional[Dict[str, Any]] = None,
        availability_summary: Optional[str] = None,
    ):
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._rules_text = rules_text
        self._language = language
        self._request_human_confirmation = request_human_confirmation
        self._hitl_policy = hitl_policy or "balanced"
        self._human_validation_history = human_validation_history or []
        self._available_tools = set(available_tools) if available_tools else None
        self._production_skills = set(production_skills) if production_skills else None
        self._perception_tools = set(perception_tools) if perception_tools else None
        self._tool_returns = dict(tool_returns) if tool_returns else {}
        self._availability_summary = availability_summary or ""

    def _summarize_human_validation_history(self) -> str:
        """Synthétise l'historique des arbitrages humains survenus durant la mission courante."""
        if not self._human_validation_history:
            return _("(aucun arbitrage humain préalable pour cette mission)")
        lines = []
        for idx, entry in enumerate(self._human_validation_history, 1):
            status = _("✅ APPROUVÉ") if entry.get("approved") else _("❌ REFUSÉ")
            steps = entry.get("steps", [])
            steps_str = "; ".join(steps[:5]) if steps else _("(étapes non spécifiées)")
            feedback = entry.get("user_feedback", "")
            feedback_str = f" | Note utilisateur: '{feedback}'" if feedback else ""
            lines.append(
                f"- Arbitrage #{idx} : {status} [Risque: {entry.get('risk_level', 'inconnu')}] "
                f"— Objectif: '{entry.get('goal', '')}' — Actions: {steps_str}{feedback_str}"
            )
        return "\n".join(lines)

    def _check_implicit_validation(self, plan: Plan, decision: PlanValidationDecision) -> bool:
        """
        Vérifie si les actions sensibles du plan bénéficient d'un consentement implicite
        déjà accordé par l'utilisateur lors d'une tentative précédente de cette même mission.
        """
        if not self._human_validation_history:
            return False

        # 1. Prudence : Si un refus utilisateur récent a été enregistré dans cette mission,
        # on ne permet pas de validation implicite aveugle
        for entry in self._human_validation_history:
            if not entry.get("approved", False):
                return False

        # 2. Récupérer l'ensemble des outils sensibles et descriptions autorisés
        approved_tools: Set[str] = set()
        approved_steps_text: List[str] = []
        for entry in self._human_validation_history:
            if entry.get("approved"):
                for t in entry.get("tools", []):
                    if t:
                        approved_tools.add(t)
                for s in entry.get("steps", []):
                    if s:
                        approved_steps_text.append(_normalize_text(s))

        # 3. Récupérer les étapes sensibles du plan courant
        flagged_step_ids = set(decision.irreversibility_flags or [])
        sensitive_current_steps = [
            s for s in plan.steps
            if s.id in flagged_step_ids or getattr(s, "is_irreversible", False)
        ]

        if not sensitive_current_steps:
            return True

        # 3b. Clé stable inter-replans : mêmes outils sensibles déjà approuvés
        # (un échec purement technique ne rouvre pas la validation).
        current_tools = {
            (getattr(s, "tool_name", "") or "") for s in sensitive_current_steps
        } - {""}
        if current_tools and current_tools <= approved_tools:
            return True

        # 4. Vérifier la convergence pour chaque étape sensible
        for step in sensitive_current_steps:
            tool = getattr(step, "tool_name", "") or ""
            desc = _normalize_text(step.description)

            # Si l'outil utilisé est déjà expressément approuvé
            if tool and tool in approved_tools:
                continue

            # Ou si la description converge avec une action déjà approuvée
            matched = False
            for app_desc in approved_steps_text:
                if app_desc and (desc in app_desc or app_desc in desc):
                    matched = True
                    break
            if matched:
                continue

            # Une action sensible inédite ou non couverte a été trouvée
            return False

        return True

    # =====================================================
    # 1. Détection de motifs récursifs (déterministe, sans LLM)
    # =====================================================

    @staticmethod
    def _plan_step_signature(steps: list) -> tuple:
        """
        Signature STRUCTURELLE d'un plan : (type, tool_name) par étape.
        """
        sig = []
        for s in steps:
            if isinstance(s, dict):
                sig.append((s.get("type"), s.get("tool_name")))
            else:
                step_type = getattr(s.type, "value", s.type)
                sig.append((step_type, getattr(s, "tool_name", None)))
        return tuple(sig)

    def detect_repeated_plan_pattern(
        self, plan: Plan, previous_attempts: List[PlanAttempt]
    ) -> Optional[str]:
        """
        Compare le plan proposé aux tentatives ÉCHOUÉES précédentes de ce
        Solver. Retourne un avertissement textuel si la même structure échouée est répétée.
        """
        if not previous_attempts:
            return None

        current_sig = self._plan_step_signature(plan.steps)
        repeats = 0
        for attempt in previous_attempts:
            if getattr(attempt, "outcome", None) != "failed":
                continue
            proposed = getattr(attempt, "proposed_plan", None)
            if not proposed:
                continue
            past_steps = proposed.get("steps", [])
            if not past_steps:
                continue
            if self._plan_step_signature(past_steps) == current_sig:
                repeats += 1

        if repeats == 0:
            return None

        return _(
            "⚠️ RÉPÉTITION D'ÉCHEC : Ce plan a EXACTEMENT la même structure (types d'étapes et outils, dans le "
            "même ordre) que {repeats} tentative(s) précédente(s) de ce Solver, déjà en "
            "échec sans adaptation."
        ).format(repeats=repeats)

    def detect_lazy_delegation_and_tree_recursion(
        self,
        plan: Plan,
        target_goal: str,
        mission_history_tree: Optional[Any] = None,
    ) -> List[str]:
        """
        Détecte les patterns de délégation récursive stérile :
        - Un Solver qui délègue sa propre tâche à un sous-solver sans décomposition.
        - Un Solver qui délègue à une sous-tâche déjà tentée/échouée dans l'arbre d'exécution.
        - Un plan à étape unique de délégation paresseuse.
        """
        warnings: List[str] = []
        norm_target_goal = _normalize_text(target_goal)

        # 1. Collecter tous les objectifs déjà présents dans l'arbre d'exécution
        tree_goals: List[Tuple[str, str, str]] = []  # (norm_goal, raw_goal, status)
        if mission_history_tree:
            def collect_goals(tree: Any):
                g = getattr(tree, "goal", "")
                st = getattr(tree, "status", "")
                if g:
                    tree_goals.append((_normalize_text(g), g, st))
                for attempt in getattr(tree, "attempts", []) or []:
                    for node in getattr(attempt, "nodes", []) or []:
                        child_tree = getattr(node, "child_execution_tree", None)
                        if child_tree:
                            collect_goals(child_tree)

            collect_goals(mission_history_tree)

        # 2. Analyser chaque étape de type abstract_task du plan proposé
        abstract_steps = []
        for step in plan.steps:
            stype = getattr(step.type, "value", step.type)
            if stype == "abstract_task" or step.type == StepType.ABSTRACT_TASK:
                abstract_steps.append(step)

        # Cas critique : plan à étape unique qui ne fait que déléguer
        if len(plan.steps) == 1 and len(abstract_steps) == 1:
            step = abstract_steps[0]
            norm_step_desc = _normalize_text(step.description)
            if norm_step_desc == norm_target_goal or (norm_target_goal and norm_target_goal in norm_step_desc):
                warnings.append(
                    _(
                        "⚠️ DÉLÉGATION PARESSEUSE UNIQUE : Le plan se résume à une seule sous-tâche ('{desc}') "
                        "qui transfère intégralement l'objectif courant ('{goal}') à un sous-solver sans "
                        "aucune décomposition ni action concrète."
                    ).format(desc=step.description, goal=target_goal)
                )

        for step in abstract_steps:
            norm_desc = _normalize_text(step.description)
            if not norm_desc:
                continue

            # Auto-délégation directe
            if norm_desc == norm_target_goal:
                warnings.append(
                    _(
                        "⚠️ AUTO-DÉLÉGATION RÉCURSIVE : L'étape `{step_id}` délègue la sous-tâche '{desc}' "
                        "qui est STRICTEMENT IDENTIQUE à l'objectif du Solver courant. Un Solver ne doit "
                        "pas déléguer son propre mandat sans le décomposer."
                    ).format(step_id=step.id, desc=step.description)
                )
                continue

            # Récursion d'arbre : sous-tâche déjà tentée dans la hiérarchie
            for norm_tg, raw_tg, st in tree_goals:
                if norm_desc == norm_tg and norm_tg != norm_target_goal:
                    warnings.append(
                        _(
                            "⚠️ RÉCURSION D'ARBRE D'EXÉCUTION : L'étape `{step_id}` propose de déléguer '{desc}' "
                            "alors que cet objectif exact a déjà été exécuté dans l'arbre de la mission (statut: {status})."
                        ).format(step_id=step.id, desc=step.description, status=st)
                    )
                    break

        return warnings

    @staticmethod
    def summarize_mission_history(
        root_tree: Optional[Any],
        max_display_depth: int = 5,
        max_nodes_per_attempt: int = 10,
    ) -> str:
        """
        Vue synthétique et claire de l'arbre d'exécution de la mission.
        Permet au LLM Judge de lire littéralement la hiérarchie des Solvers et sous-tâches.
        """
        if not root_tree:
            return _("(aucun historique disponible — tout premier plan de la mission)")

        lines: List[str] = []

        def walk(tree: Any, indent: int = 0) -> None:
            depth = getattr(tree, "depth", indent // 2)
            if depth > max_display_depth:
                lines.append("  " * indent + _("… (profondeur max d'affichage atteinte)"))
                return

            prefix = "  " * indent
            status = getattr(tree, "status", "?")
            goal = getattr(tree, "goal", "?")
            solver_id = getattr(tree, "solver_id", "?")
            lines.append(f"{prefix}🌳 [Niveau {depth}] Solver `{solver_id}` [{status}] : {goal}")

            attempts = getattr(tree, "attempts", None) or []
            for attempt in attempts:
                outcome = getattr(attempt, "outcome", "?")
                if outcome in ("in_progress", "pending") and len(attempts) > 1:
                    continue
                failure_reason = getattr(attempt, "failure_reason", None)
                suffix = f" -> ÉCHEC : {failure_reason}" if outcome == "failed" and failure_reason else ""
                lines.append(f"{prefix}   ↳ Tentative {getattr(attempt, 'attempt_number', 1)} [{outcome}]{suffix}")

                nodes = getattr(attempt, "nodes", None) or []
                for node in nodes[:max_nodes_per_attempt]:
                    node_desc = getattr(node, "description", "") or ""
                    node_type = getattr(node, "step_type", "")
                    node_status = getattr(node, "status", "?")
                    lines.append(f"{prefix}     • [{node_type}] {node_desc} ({node_status})")
                    child = getattr(node, "child_execution_tree", None)
                    if child:
                        walk(child, indent + 2)

                if len(nodes) > max_nodes_per_attempt:
                    lines.append(
                        f"{prefix}     … ({len(nodes) - max_nodes_per_attempt} étape(s) supplémentaire(s))"
                    )

        walk(root_tree)
        return "\n".join(lines) if lines else _("(historique vide)")

    # =====================================================
    # 2 & 3. Jugement LLM + confirmation humaine si nécessaire
    # =====================================================

    @staticmethod
    def hitl_policy_text(policy: str) -> str:
        """Seul le bloc du mode actif est injecté (pas les 3, anti-volume).

        Fonction pure, testée vite.
        """
        p = str(policy or "balanced").strip().lower()
        if p == "strict":
            return _("Mode strict : exige l'accord humain pour toute étape critique/irréversible, sans exception. Étapes lecture seule = risque bas, sans confirmation.")
        if p == "autonomous":
            return _("Mode autonomous : passe sans interrompre l'utilisateur (requires_human_confirmation: false). Remplis quand même risk_level et irreversibility_flags.")
        return _("Mode balanced : hérite du consentement si l'utilisateur a déjà approuvé ces actions/outils dans cette mission sans nouveau risque ni nouvel outil sensible ; sinon exige la confirmation humaine.")

    def _summarize_plan_for_prompt(self, plan: Plan) -> str:
        """
        Résumé du plan : objectif, type d'étape, outil appelé, description,
        ARGUMENTS (tronqués) et condition. Le juge ne peut pas voir un correctif
        (ex : touche `lwin` → `WIN`) si on lui cache les arguments : sans eux,
        tout replan ressemble à une redite et se fait refuser à tort.
        """
        lines = [f"Objectif déclaré du plan : {plan.goal}"]
        for step in plan.steps:
            marker = " ⚠️ DÉCLARÉ IRRÉVERSIBLE" if getattr(step, "is_irreversible", False) else ""
            reason = f" ({step.irreversibility_reason})" if getattr(step, "irreversibility_reason", None) else ""
            tool = f" [outil: {step.tool_name}]" if getattr(step, "tool_name", None) else ""
            step_type_str = step.type.value if hasattr(step.type, 'value') else str(step.type)
            args = _parse_step_args(getattr(step, "tool_args_json", "{}"))
            args_str = ""
            if args:
                try:
                    import json as _json2
                    args_str = f" args={_json2.dumps(args, ensure_ascii=False)[:150]}"
                except Exception:
                    args_str = ""
            cond = f" [SI {step.execute_if}]" if getattr(step, "execute_if", None) else ""
            out_var = f" -> {step.output_variable_name}" if getattr(step, "output_variable_name", None) else ""
            exp = f" attend={step.expected_result}" if getattr(step, "expected_result", None) else ""
            lines.append(f"- {step.id} [{step_type_str}]{tool} : {step.description}{args_str}{cond}{out_var}{exp}{marker}{reason}")
        return "\n".join(lines)

    @staticmethod
    def build_repetition_fact(plan: Any, previous_attempts: Optional[List[Any]] = None) -> Optional[str]:
        """Un seul fait de répétition, typé (pas un signal flou).

        Distingue les plans JETÉS avant exécution (pas chers, le planner peut
        être juste bloqué) des plans ÉCHOUÉS à l'exécution (vrai coût). Le juge
        ne doit pas traiter un rejet de syntaxe comme une preuve d'échec.
        Fonction pure, testée vite.
        """
        failed = [a for a in (previous_attempts or []) if getattr(a, "outcome", None) == "failed"]
        if not failed:
            return None
        current_sig = PlanValidator._plan_step_signature(list(getattr(plan, "steps", []) or []))
        preexec = 0
        executed = 0
        for attempt in failed:
            proposed = getattr(attempt, "proposed_plan", None) or {}
            steps = proposed.get("steps", []) if isinstance(proposed, dict) else []
            if PlanValidator._plan_step_signature(steps) != current_sig:
                continue
            nodes = getattr(attempt, "nodes", None)
            if nodes:
                executed += 1
            else:
                preexec += 1
        if not preexec and not executed:
            return None
        parts = []
        if executed:
            parts.append(_("{n} échec(s) À L'EXÉCUTION à structure égale").format(n=executed))
        if preexec:
            parts.append(_("{n} rejet(s) AVANT exécution à structure égale (pas chers, pas une preuve d'échec)").format(n=preexec))
        return _("Répétition : ") + " ; ".join(parts) + "."

    @staticmethod
    def build_direct_perception_note(plan: Any, perception_tool_names=None, tool_returns=None) -> Optional[str]:
        """Fait moteur sur les appels perception directs (le juge n'a plus à deviner).

        Liste les étapes concernées et rappelle l'exception pixels : un outil à
        charge média déclarée peut être appelé en direct sans tuer les pixels.
        """
        ids = find_direct_perception_calls(plan, perception_tool_names, tool_returns)
        if not ids:
            return None
        return _("Appels perception directs : {ids} (hors exception pixels : charge média déclarée = appel direct légitime, les pixels sont préservés).").format(ids=", ".join(ids))

    async def validate(
        self,
        plan: Plan,
        child_solver_id: str,
        target_goal: str,
        previous_attempts: Optional[List[PlanAttempt]] = None,
        mission_history_tree: Optional[Any] = None,
    ) -> PlanValidationOutcome:
        # Collecter les signaux déterministes
        repeated_warning = self.detect_repeated_plan_pattern(plan, previous_attempts or [])
        recursion_warnings = self.detect_lazy_delegation_and_tree_recursion(
            plan=plan,
            target_goal=target_goal,
            mission_history_tree=mission_history_tree,
        )

        all_warnings = []
        if repeated_warning:
            all_warnings.append(repeated_warning)
        all_warnings.extend(recursion_warnings)
        pattern_warning = "\n\n".join(all_warnings) if all_warnings else None

        # Nouveauté calculée (fait déterministe) : le juge voit les arguments
        # ET sait ce qui a changé vs les échecs. Sans ça, un correctif
        # d'argument ressemble à une redite et se fait refuser à tort.
        novelty_assessment = compare_plan_novelty(plan, previous_attempts or [])
        repetition_fact = self.build_repetition_fact(plan, previous_attempts or [])
        direct_perception_note = self.build_direct_perception_note(
            plan, self._perception_tools, self._tool_returns)

        # Gate déterministe (fail-fast) : UNIQUEMENT les cas sûrs à 100%,
        # zéro faux positif, pas chers. Doctrine : un rejet coûte une tentative
        # + tokens, donc le doute stylistique reste un conseil dans le prompt,
        # jamais un refus code.
        # Gardées : outil/skill inexistant, args JSON illisibles, outil réservé,
        # perception directe (réparée auto, pas rejetée en boucle).
        # VOLONTAIREMENT ABSENTE : aucune gate sur `direct_answer` sans variable.
        # Un refus honnête (pas d'outil, modalité non supportée, incohérence) ou
        # une réponse avec `execute_if` n'a besoin d'aucune variable et reste
        # valide. Le filet final `is_mission_success` (1 action matérielle
        # réussie exigée) suffit contre les faux succès.
        unknown = find_unknown_plan_tools(
            plan, self._available_tools, self._production_skills
        )
        malformed = find_malformed_step_args(plan)
        reserved = find_reserved_plan_tools(plan)
        direct_perception = find_direct_perception_calls(plan, self._perception_tools, self._tool_returns)
        problems = [f"inconnu:{u}" for u in unknown]
        problems += [f"args illisibles étape:{s}" for s in malformed]
        problems += [f"outil réservé au validateur étape:{s}" for s in reserved]
        problems += [
            f"perception directe interdite étape:{s} "
            "(lire le monde uniquement via perceive_understand)"
            for s in direct_perception
        ]
        if problems:
            details = ", ".join(problems[:8])
            return PlanValidationOutcome(
                is_valid=False,
                reason=_(
                    "Plan refusé sans appel au juge : {details}. "
                    "N'utilisez que les outils listés et les skills en PRODUCTION, "
                    "avec des arguments JSON valides ; "
                    "sinon terminez en réponse directe motivée."
                ).format(details=details),
                risk_level=RiskLevel.MEDIUM,
            )

        mission_history_summary = self.summarize_mission_history(mission_history_tree)
        declared_irreversible = [s.id for s in plan.steps if getattr(s, "is_irreversible", False)]

        prompt = self._prompt_loader.load(
            "plan_validation.md",
            lang=self._language,
            goal=target_goal,
            plan_summary=self._summarize_plan_for_prompt(plan),
            rules=self._rules_text or _("(rules.md absent ou vide — aucun critère explicite fourni.)"),
            pattern_warning=pattern_warning,
            novelty_assessment=novelty_assessment,
            repetition_fact=repetition_fact,
            direct_perception_note=direct_perception_note,
            hitl_policy_text=self.hitl_policy_text(self._hitl_policy),
            mission_history_summary=mission_history_summary,
            declared_irreversible_steps=declared_irreversible,
            hitl_policy=self._hitl_policy,
            human_validation_history=self._summarize_human_validation_history(),
            availability_summary=self._availability_summary or _(
                "(disponibilités non transmises — jugez sur le plan seul)"
            ),
        )

        try:
            decision: PlanValidationDecision = await self._llm.generate_structured(
                prompt=prompt,
                schema=PlanValidationDecision,
                tag="PlanValidationDecision",
            )
        except Exception as e:
            Logger.error(f"[PlanValidator] Échec de l'appel LLM de validation : {e}")
            return PlanValidationOutcome(
                is_valid=False,
                reason=_(
                    "Le juge de conformité (LLM) a échoué : {error}. Plan refusé par prudence."
                ).format(error=str(e)),
                risk_level=RiskLevel.CRITICAL,
            )

        if not decision.is_conformant:
            return PlanValidationOutcome(
                is_valid=False,
                reason=decision.reason,
                risk_level=decision.risk_level,
                irreversibility_flags=decision.irreversibility_flags,
            )

        # Gestion des politiques HITL (Human-in-the-loop) et validation implicite
        if decision.requires_human_confirmation:
            if self._hitl_policy == "autonomous":
                Logger.info("[PlanValidator] 🤖 Mode HITL 'autonomous' : confirmation humaine contournée.")
                decision.requires_human_confirmation = False
            elif self._hitl_policy == "balanced":
                if self._check_implicit_validation(plan, decision):
                    Logger.info(
                        "[PlanValidator] ⚡ Validation implicite appliquée (mode balanced) : les actions sensibles "
                        "du plan convergent avec un arbitrage favorable déjà consenti par l'utilisateur lors de cette mission."
                    )
                    decision.requires_human_confirmation = False

        if not decision.requires_human_confirmation:
            return PlanValidationOutcome(
                is_valid=True,
                reason=decision.reason,
                risk_level=decision.risk_level,
                irreversibility_flags=decision.irreversibility_flags,
            )

        # Conforme, mais le juge exige une confirmation humaine.
        if self._request_human_confirmation is None:
            return PlanValidationOutcome(
                is_valid=False,
                reason=_(
                    "Ce plan nécessite une confirmation humaine ({reason}) mais aucun canal "
                    "de confirmation n'est disponible. Plan refusé par prudence."
                ).format(reason=decision.reason),
                risk_level=decision.risk_level,
                requires_human_confirmation=True,
                irreversibility_flags=decision.irreversibility_flags,
            )

        try:
            confirmed = await self._request_human_confirmation(plan, decision)
        except Exception as e:
            Logger.error(f"[PlanValidator] Échec de la demande de confirmation humaine : {e}")
            confirmed = False

        if not confirmed:
            return PlanValidationOutcome(
                is_valid=False,
                reason=_("Confirmation humaine refusée ou indisponible pour : {reason}").format(
                    reason=decision.reason
                ),
                risk_level=decision.risk_level,
                requires_human_confirmation=True,
                human_confirmed=False,
                irreversibility_flags=decision.irreversibility_flags,
            )

        return PlanValidationOutcome(
            is_valid=True,
            reason=decision.reason,
            risk_level=decision.risk_level,
            requires_human_confirmation=True,
            human_confirmed=True,
            irreversibility_flags=decision.irreversibility_flags,
        )


class DepthEscalationOutcome:
    """Résultat du jugement sur une demande d'extension de profondeur."""

    def __init__(self, approved: bool, reason: str):
        self.approved = approved
        self.reason = reason

    def __bool__(self) -> bool:
        return self.approved

    def __repr__(self) -> str:
        return f"DepthEscalationOutcome(approved={self.approved}, reason={self.reason!r})"


def summarize_ancestor_chain(ancestor_chain: List[Dict[str, Any]]) -> str:
    """
    Formate la chaîne d'ancêtres (racine -> Solver courant) pour le prompt
    du juge. Chaque maillon ne porte que profondeur/goal/id — pas de détail
    d'exécution (déjà hors sujet pour CE jugement, qui porte sur la
    progression logique entre niveaux, pas sur le contenu technique).
    """
    if not ancestor_chain:
        return "(chaîne vide)"
    lines = []
    for link in ancestor_chain:
        lines.append(f"- Profondeur {link.get('depth')} (solver `{link.get('solver_id')}`) : {link.get('goal')}")
    return "\n".join(lines)


async def review_depth_escalation(
    llm: Any,
    prompt_loader: Any,
    language: str,
    ancestor_chain: List[Dict[str, Any]],
) -> DepthEscalationOutcome:
    """
    Fonction autonome (pas une méthode de PlanValidator, volontairement —
    ce jugement ne dépend d'aucun état de PlanValidator comme rules_text ou
    le callback de confirmation humaine, donc pas besoin d'instancier la
    classe pour ça) qui demande au juge si une chaîne de sous-tâches
    imbriquées (abstract_task) reflète une décomposition légitime ou un
    motif récursif dégénéré.
    """
    prompt = prompt_loader.load(
        "depth_escalation_review.md",
        lang=language,
        ancestor_chain_summary=summarize_ancestor_chain(ancestor_chain),
        depth_reached=len(ancestor_chain),
    )
    try:
        decision: DepthEscalationDecision = await llm.generate_structured(
            prompt=prompt,
            schema=DepthEscalationDecision,
            tag="DepthEscalationDecision",
        )
    except Exception as e:
        Logger.error(f"[PlanValidator] Échec du jugement d'extension de profondeur : {e}")
        return DepthEscalationOutcome(
            approved=False,
            reason=_("Le juge a échoué : {error}. Extension refusée par prudence.").format(error=str(e)),
        )
    return DepthEscalationOutcome(approved=decision.is_legitimate_complexity, reason=decision.reason)


class RetryExtensionOutcome:
    """Résultat du jugement sur une demande de rallonge d'exécution."""

    def __init__(self, approved: bool, reason: str):
        self.approved = approved
        self.reason = reason

    def __bool__(self) -> bool:
        return self.approved

    def __repr__(self) -> str:
        return f"RetryExtensionOutcome(approved={self.approved}, reason={self.reason!r})"


async def review_retry_extension(
    llm: Any,
    prompt_loader: Any,
    language: str,
    progress_summary: str,
) -> RetryExtensionOutcome:
    """
    Fonction autonome (miroir de review_depth_escalation) : le budget
    standard est épuisé, le progrès accompli justifie-t-il UNE tentative
    de plus ? Échec du juge = refus par prudence.
    """
    from core.plan_models import RetryExtensionDecision
    prompt = prompt_loader.load(
        "retry_extension_review.md",
        lang=language,
        progress_summary=progress_summary or _("(aucun progrès enregistré)"),
    )
    try:
        decision: RetryExtensionDecision = await llm.generate_structured(
            prompt=prompt,
            schema=RetryExtensionDecision,
            tag="RetryExtensionDecision",
        )
    except Exception as e:
        Logger.error(f"[PlanValidator] Échec du jugement de rallonge d'exécution : {e}")
        return RetryExtensionOutcome(
            approved=False,
            reason=_("Le juge a échoué : {error}. Rallonge refusée par prudence.").format(error=str(e)),
        )
    return RetryExtensionOutcome(approved=decision.is_worthwhile, reason=decision.reason)

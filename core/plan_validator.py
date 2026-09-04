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
    ):
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._rules_text = rules_text
        self._language = language
        self._request_human_confirmation = request_human_confirmation
        self._hitl_policy = hitl_policy or "balanced"
        self._human_validation_history = human_validation_history or []

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

    def _summarize_plan_for_prompt(self, plan: Plan) -> str:
        """
        Résumé épuré du plan : objectif, type d'étape, outil appelé, description et flags d'irréversibilité.
        Aucun bruit de syntaxe de variables, de nommage ou de tuyauterie interne.
        """
        lines = [f"Objectif déclaré du plan : {plan.goal}"]
        for step in plan.steps:
            marker = " ⚠️ DÉCLARÉ IRRÉVERSIBLE" if getattr(step, "is_irreversible", False) else ""
            reason = f" ({step.irreversibility_reason})" if getattr(step, "irreversibility_reason", None) else ""
            tool = f" [outil: {step.tool_name}]" if getattr(step, "tool_name", None) else ""
            step_type_str = step.type.value if hasattr(step.type, 'value') else str(step.type)
            lines.append(f"- {step.id} [{step_type_str}]{tool} : {step.description}{marker}{reason}")
        return "\n".join(lines)

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

        mission_history_summary = self.summarize_mission_history(mission_history_tree)
        declared_irreversible = [s.id for s in plan.steps if getattr(s, "is_irreversible", False)]

        prompt = self._prompt_loader.load(
            "plan_validation.md",
            lang=self._language,
            goal=target_goal,
            plan_summary=self._summarize_plan_for_prompt(plan),
            rules=self._rules_text or _("(rules.md absent ou vide — aucun critère explicite fourni.)"),
            pattern_warning=pattern_warning,
            mission_history_summary=mission_history_summary,
            declared_irreversible_steps=declared_irreversible,
            hitl_policy=self._hitl_policy,
            human_validation_history=self._summarize_human_validation_history(),
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

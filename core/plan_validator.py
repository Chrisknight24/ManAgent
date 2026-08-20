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

import difflib
from typing import List, Optional, Callable, Awaitable, Any, Dict
from core.plan_models import Plan, PlanValidationDecision, RiskLevel, DepthEscalationDecision
from core.execution_models import PlanAttempt
from core.i18n import _
from utils.logger import Logger


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
    ):
        """
        llm : objet exposant `generate_structured(prompt, schema, tag=...)`.
        prompt_loader : objet exposant `load(template_name, lang=..., **kwargs)`.
        rules_text : contenu brut de rules.md (chaîne vide si absent — la
            validation continue quand même, sans critères explicites, plutôt
            que de bloquer toute mission faute de fichier).
        request_human_confirmation : callback optionnel appelé quand le LLM
            juge qu'une confirmation humaine est requise. None = pas de canal
            disponible (refus par prudence systématique dans ce cas).
        """
        self._llm = llm
        self._prompt_loader = prompt_loader
        self._rules_text = rules_text
        self._language = language
        self._request_human_confirmation = request_human_confirmation

    # =====================================================
    # 1. Détection de motifs récursifs (déterministe, sans LLM)
    # =====================================================

    @staticmethod
    def _plan_step_signature(steps: list) -> tuple:
        """
        Signature STRUCTURELLE d'un plan : (type, tool_name) par étape.
        Volontairement insensible à la formulation (description, step_context)
        qui varie d'une tentative à l'autre même quand le Planner retente,
        dans les faits, la même approche.
        Accepte aussi bien des PlanStep (tentative courante) que des dicts
        (tentatives passées, désérialisées depuis PlanAttempt.proposed_plan).
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
        Solver. Retourne un avertissement textuel si un pattern récursif est
        détecté (même structure qu'au moins une tentative déjà en échec),
        sinon None.
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
            "⚠️ Ce plan a EXACTEMENT la même structure (types d'étapes et outils, dans le "
            "même ordre) que {repeats} tentative(s) précédente(s) de ce Solver, déjà en "
            "échec. Il est probable que le Planner ignore le feedback d'échec plutôt que "
            "d'adapter son approche."
        ).format(repeats=repeats)

    @staticmethod
    def _goal_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    def detect_ancestor_goal_recursion(
        self,
        plan: Plan,
        ancestor_chain: Optional[List[Dict[str, Any]]],
        similarity_threshold: float = 0.55,
    ) -> Optional[str]:
        """
        Compare l'objectif du plan (et celui de ses étapes abstract_task,
        s'il en a) aux objectifs des Solvers ANCÊTRES (racine -> parent
        direct). Contrairement à detect_repeated_plan_pattern (intra-solver,
        basé sur previous_attempts), ceci détecte un motif confirmé en test
        réel : une chaîne d'abstract_task imbriqués qui redélègue, niveau
        après niveau, une reformulation du MÊME objectif sans jamais produire
        d'action concrète — invisible à la comparaison intra-solver puisque
        chaque niveau est un Solver flambant neuf dont les previous_attempts
        sont toujours vides au premier essai. Similarité floue (difflib)
        plutôt qu'égalité stricte, pour attraper les reformulations
        ("fermer les applications" / "fermer l'ensemble des fenêtres et
        processus applicatifs cibles"...).
        """
        if not ancestor_chain:
            return None

        matches = []
        for ancestor in ancestor_chain:
            ancestor_goal = ancestor.get("goal") or ""
            sim = self._goal_similarity(plan.goal, ancestor_goal)
            if sim >= similarity_threshold:
                matches.append((ancestor.get("depth"), ancestor_goal, sim))
            for step in plan.steps:
                if getattr(step, "type", None) and getattr(step.type, "value", None) == "abstract_task":
                    step_sim = self._goal_similarity(step.description, ancestor_goal)
                    if step_sim >= similarity_threshold:
                        matches.append((ancestor.get("depth"), ancestor_goal, step_sim))

        if not matches:
            return None

        best = max(matches, key=lambda m: m[2])
        return _(
            "⚠️ L'objectif de ce plan (ou l'une de ses étapes abstract_task) ressemble "
            "fortement (similarité {sim:.0%}) à celui d'un ANCÊTRE à la profondeur {depth} : "
            "« {goal} ». Chaîne actuelle : {chain_len} niveau(x). Si ce plan ne fait que "
            "redéléguer le même objectif sans action concrète nouvelle par rapport à ce "
            "niveau ancêtre, c'est un motif récursif dégénéré, pas une décomposition légitime."
        ).format(sim=best[2], depth=best[0], goal=best[1], chain_len=len(ancestor_chain))

    # =====================================================
    # 2 & 3. Jugement LLM + confirmation humaine si nécessaire
    # =====================================================

    def _summarize_plan_for_prompt(self, plan: Plan) -> str:
        lines = [f"Objectif du plan : {plan.goal}"]
        for step in plan.steps:
            marker = " ⚠️ DÉCLARÉ IRRÉVERSIBLE" if getattr(step, "is_irreversible", False) else ""
            reason = f" ({step.irreversibility_reason})" if getattr(step, "irreversibility_reason", None) else ""
            tool = f" outil={step.tool_name}" if getattr(step, "tool_name", None) else ""
            # BUG CORRIGÉ : execute_if n'était jamais montré au juge. Deux
            # étapes gardées par des conditions mutuellement exclusives
            # (ex: "si le process a été tué" / "si le process n'a PAS été
            # tué") ressemblaient alors à deux actions contradictoires
            # exécutées inconditionnellement l'une après l'autre, ce qui
            # provoquait des refus de plans parfaitement valides.
            condition = f" [SI {step.execute_if}]" if getattr(step, "execute_if", None) else ""
            lines.append(f"- {step.id} [{step.type.value}]{tool}{condition} : {step.description}{marker}{reason}")
        return "\n".join(lines)

    async def validate(
        self,
        plan: Plan,
        child_solver_id: str,
        target_goal: str,
        previous_attempts: Optional[List[PlanAttempt]] = None,
        ancestor_chain: Optional[List[Dict[str, Any]]] = None,
    ) -> PlanValidationOutcome:
        pattern_warning = self.detect_repeated_plan_pattern(plan, previous_attempts or [])
        ancestor_warning = self.detect_ancestor_goal_recursion(plan, ancestor_chain)
        declared_irreversible = [s.id for s in plan.steps if getattr(s, "is_irreversible", False)]

        prompt = self._prompt_loader.load(
            "plan_validation.md",
            lang=self._language,
            goal=target_goal,
            plan_summary=self._summarize_plan_for_prompt(plan),
            rules=self._rules_text or _("(rules.md absent ou vide — aucun critère explicite fourni.)"),
            pattern_warning=pattern_warning,
            ancestor_warning=ancestor_warning,
            declared_irreversible_steps=declared_irreversible,
        )

        try:
            decision: PlanValidationDecision = await self._llm.generate_structured(
                prompt=prompt,
                schema=PlanValidationDecision,
                tag="PlanValidationDecision",
            )
        except Exception as e:
            Logger.error(f"[PlanValidator] Échec de l'appel LLM de validation : {e}")
            # Le juge lui-même a échoué : refus PAR PRUDENCE plutôt que de
            # laisser passer un plan qui n'a jamais été réellement jugé.
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

"""
core/solver.py
==============
Solver – exécute une mission en orchestrant Planner et Executor.
Version avec enregistrement d'un DataProvider pour le registre,
activation explicite de la Progressive Disclosure,
et vue normalisée du registre utilisant les types stockés (bool/data).
"""

from typing import Optional, Any, Dict, List
from utils.logger import Logger
from core.entity import Entity
from core.constants import Events
from .supervisor import Supervisor
from .plan_models import FeasibilityDecision, Plan, SolverResult, ExecutionStatus, MissionSignature, CompactedAdvice
from .planner import Planner
from .executor import Executor
from core.llm import Llm
from pydantic import ValidationError
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.id_generator import make_step_id
import time
from core.execution_models import ExecutionTree, PlanAttempt, FailureClass
from core.retriever import Retriever
from core.signature_extractor import SignatureExtractor
from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD, RETRIEVAL_MAX_RESULTS_INJECTED
from core.mission_compactor import MissionCompactor
from core.runtime_state import RuntimeState
from memory.mission_profile_store import MissionProfileStore
import asyncio
from core.discovery.providers.registry_provider import SolverRegistryProvider
import json
import re

MAX_DEPTH = 10
MAX_EXECUTION_TRIES = 3
MAX_PREEXECUTION_FAILURES = 3


class Solver(Supervisor, Entity):
    """
    Solver – exécute une mission.
    Hérite de Supervisor (pour la validation) et de Entity (pour l'identité et les DataProviders).
    """

    def __init__(
        self,
        solver_id: str,
        goal: str,
        parent: Supervisor,
        provider_manager,
        runtime_state,
        provider_id: str = None,
        model_id: str = None,
        llm: Optional[Llm] = None,
        depth: int = 0,
        context: str = "",
        parent_step_id: str = None
    ):
        Supervisor.__init__(self)
        Entity.__init__(
            self,
            name=solver_id,
            role="solver",
            llm=llm,
            parent=parent
        )

        self.goal = goal
        self.provider_manager = provider_manager
        self.runtime_state = runtime_state
        self.depth = depth
        self.context = context
        self.id = solver_id
        self.parent_step_id = parent_step_id
        self._preexecution_failures = 0
        self.execution_tree = None
        self.current_attempt = None
        self.signatures = []
        self._similar_missions = None

        if not self.llm and provider_id and model_id:
            self.llm = Llm(
                provider_manager=provider_manager,
                provider_id=provider_id,
                model_id=model_id,
                runtime_state=runtime_state
            )

        if not self.llm:
            raise ValueError(_("Solver requires a Llm instance or provider/model identifiers."))

        if isinstance(parent, Solver):
            self.variable_registry = dict(parent.variable_registry)
        else:
            self.variable_registry = {}

        # Enregistrement du DataProvider pour le registre
        if self.runtime_state.discovery_engine:
            registry_provider = SolverRegistryProvider(self)
            self.register_data_provider("registry", registry_provider)
            Logger.info(
                _("[Solver:{id}] DataProvider 'registry' enregistré pour le Solver.")
                .format(id=self.id)
            )

            # Activation explicite de la Progressive Disclosure
            if not self.llm._discovery_enabled:
                self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                Logger.info(
                    _("[Solver:{id}] Progressive Disclosure activée.")
                    .format(id=self.id)
                )

            # --- NOUVEAU : partager le registre avec le LLM pour la Progressive Disclosure ---
            if self.llm and self.runtime_state.discovery_engine:
                self.llm.set_data_context(self.variable_registry)

        self.planner = Planner(
            name=f"planner_{solver_id}",
            llm=self.llm,
            runtime_state=self.runtime_state,
            parent=self
        )
        self.executor = Executor(solver_node=self)

    def _get_registry_metadata_view(self) -> Dict[str, Any]:
        view = {}
        for key, info in self.variable_registry.items():
            value = info.get("value")
            var_type = info.get("type", self._get_type_string(value))

            if var_type == "bool":
                hint = f"(bool: {str(value).lower()})"
            elif var_type == "data":
                hint = _("(donnée masquée – utilisez Progressive Disclosure pour inspecter)")
            else:
                # Fallback
                hint = _("(donnée cachée, accessible via Progressive Disclosure)")
                if isinstance(value, dict):
                    keys = list(value.keys())[:3]
                    hint = _("(objet JSON avec clés: {keys})").format(keys=", ".join(keys))
                elif isinstance(value, list):
                    hint = _("(liste de {count} éléments)").format(count=len(value))
                elif isinstance(value, str):
                    if len(value) > 100:
                        hint = _("(chaîne de {length} caractères)").format(length=len(value))
                    else:
                        hint = _("(chaîne: {preview}...)").format(preview=value[:50])
                elif value is None:
                    hint = _("(null)")
                else:
                    hint = _("(type: {type})").format(type=type(value).__name__)

            view[key] = {
                "description": info.get("description", _("Pas de description")),
                "source": info.get("source", _("Inconnu")),
                "timestamp": info.get("timestamp", "N/A"),
                "type": var_type,
                "value_hint": hint,
            }
        return view

    def _get_type_string(self, value) -> str:
        """Méthode de fallback pour déduire le type si non stocké."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        return "unknown"

    def assign_signatures(self, signatures: List[MissionSignature]) -> None:
        self.signatures = signatures
        self.runtime_state.solver_registry[self.id] = {
            "signatures": signatures,
            "similar_missions": None
        }
        Logger.debug(f"[Solver:{self.id}] Signatures assignées : {len(signatures)}")

    async def run(self) -> SolverResult:
        with self.runtime_state.execution_context.scope(
            solver_id=self.id,
            depth=self.depth,
            parent_solver_id=self.parent.id if hasattr(self.parent, 'id') else None,
            attempt_number=None,
        ):
            if self.runtime_state.cancel_requested:
                return SolverResult(
                    status=ExecutionStatus.FAILED,
                    final_context=self.context,
                    error_reason=_("Génération annulée")
                )

            self.execution_tree = ExecutionTree(
                solver_id=self.id,
                goal=self.goal,
                parent_solver_id=self.parent.id if hasattr(self.parent, 'id') else None,
                parent_step_id=self.parent_step_id,
                depth=self.depth,
                started_at=time.time(),
                status="failed"
            )

            proposed_plan = None  # Initialisation avant le try

            try:
                similar_missions_context = None

                if not self.signatures:
                    try:
                        extractor = SignatureExtractor(llm=self.llm, runtime_state=self.runtime_state)
                        signatures = await extractor.extract(self.goal, self.context)
                        self.assign_signatures(signatures)
                        Logger.info(f"[Solver:{self.id}] {len(signatures)} signature(s) générée(s).")
                    except Exception as e:
                        Logger.error(f"[Solver:{self.id}] Échec de SignatureExtractor : {e}")

                similar_missions_context = None
                self._similar_missions = None

                if self.signatures:
                    registry_entry = self.runtime_state.solver_registry.get(self.id)
                    if registry_entry and registry_entry.get("similar_missions") is not None:
                        similar = registry_entry["similar_missions"]
                        if similar:
                            Logger.info(f"[Solver:{self.id}] Cache utilisé : {len(similar)} missions.")
                        else:
                            similar = None
                    else:
                        try:
                            retriever = Retriever(
                                runtime_state=self.runtime_state,
                                top_k=RETRIEVAL_TOP_K,
                                threshold=RETRIEVAL_THRESHOLD,
                                cache_manager=self.runtime_state.cache_manager
                            )
                            similar = await retriever.retrieve(
                                self.signatures,
                                query_mission_id=self.id
                            )
                            if registry_entry:
                                registry_entry["similar_missions"] = similar
                            else:
                                self.runtime_state.solver_registry[self.id] = {
                                    "signatures": self.signatures,
                                    "similar_missions": similar
                                }
                        except Exception as e:
                            Logger.error(f"[Solver:{self.id}] Échec du retrieval : {e}")

                    self._similar_missions = similar

                    if similar:
                        try:
                            compactor = MissionCompactor(
                                parent=self,
                                cache_manager=self.runtime_state.cache_manager
                            )
                            compacted: CompactedAdvice = await compactor.compact(
                                goal=self.goal,
                                similar_missions=similar,
                                llm=self.llm,
                                runtime_state=self.runtime_state,
                                query_signatures=self.signatures
                            )
                            compacted_advice = compacted.advice
                            is_novel = compacted.is_novel
                            self.runtime_state.update_marker("is_novel", is_novel)
                            if compacted_advice:
                                similar_missions_context = compacted_advice
                                Logger.info(f"[Solver:{self.id}] MissionCompactor a produit un conseil de {len(compacted_advice)} caractères. Nouveau ? {is_novel}")
                            else:
                                temp_compactor = MissionCompactor()
                                similar_missions_context = temp_compactor._format_missions(similar)
                                Logger.warning(f"[Solver:{self.id}] MissionCompactor a retourné vide, utilisation de la liste brute formatée.")
                        except Exception as e:
                            Logger.error(f"[Solver:{self.id}] Erreur lors du MissionCompactor : {e}")
                            temp_compactor = MissionCompactor()
                            similar_missions_context = temp_compactor._format_missions(similar)
                    else:
                        self.runtime_state.update_marker("is_novel", True)
                        similar_missions_context = None

                with self.runtime_state.execution_context.scope(entity_name="Feasibility", entity_role="Solver"):
                    decision = await self._check_feasibility(similar_missions_context)

                if not decision.is_possible:
                    Logger.warning(f"[Solver:{self.id}] 🛑 Objectif impossible. Raison : {decision.reason}")
                    self.execution_tree.ended_at = time.time()
                    self.execution_tree.status = "failed"
                    enriched_context = self.context + f"\n\n[RAISON DE L'ÉCHEC] {decision.reason}"
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=enriched_context,
                        error_reason=decision.reason,
                        execution_tree=self.execution_tree
                    )
                Logger.info(f"[Solver:{self.id}] 💡 Stratégie adoptée : {decision.refined_strategy}")

                success = False
                final_result = None
                execution_attempt = 0
                attempt_counter = 0

                while execution_attempt < MAX_EXECUTION_TRIES:
                    if self.runtime_state.cancel_requested:
                        break

                    execution_attempt += 1
                    self.runtime_state.update_marker("execution_attempt", execution_attempt)

                    Logger.event(
                        "mission_retry",
                        solver_id=self.id,
                        attempt_number=execution_attempt,
                        max_attempts=MAX_EXECUTION_TRIES
                    )

                    attempt_counter += 1
                    self.current_attempt = PlanAttempt(
                        attempt_number=attempt_counter,
                        started_at=time.time(),
                        outcome="failed",
                        failure_class=FailureClass.NONE
                    )
                    self.execution_tree.add_attempt(self.current_attempt)

                    with self.runtime_state.execution_context.scope(attempt_number=attempt_counter):
                        try:
                            with self.runtime_state.execution_context.scope(entity_name="Planner", entity_role="Solver"):
                                proposed_plan = await self.planner.propose_plan(
                                    goal=self.goal,
                                    context=self.context,
                                    strategy=decision.refined_strategy,
                                    variable_registry=self.variable_registry
                                )
                            self.current_attempt.proposed_plan = proposed_plan.model_dump(mode='json')
                            self.current_attempt.advice_injected = getattr(self.planner, "_cached_advice", None) or None

                            has_abs = any(step.type.value == "abstract_task" for step in proposed_plan.steps)
                            if has_abs:
                                self.runtime_state.update_marker("has_abstract_task", True)

                        except ValidationError as pydantic_error:
                            self.runtime_state.update_marker("plan_rejected", True)
                            error_msg = f"Erreur de validation Pydantic : {pydantic_error}"
                            Logger.warning(f"[Solver:{self.id}] ⚠️ Plan invalide (Pydantic) : {error_msg}")
                            Logger.event(
                                "plan_rejected_validation",
                                solver_id=self.id,
                                attempt=attempt_counter,
                                reason=error_msg,
                                failure_class="PLAN_REJECTED_VALIDATION"
                            )
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_VALIDATION
                            self.current_attempt.failure_reason = error_msg
                            self.current_attempt.planner_feedback = error_msg
                            self.current_attempt.target_entity = "Planner"

                            await self.propagate_event(Events.PLANNER_RETRY, {
                                "reason": error_msg,
                                "attempt": self._preexecution_failures + 1,
                                "max_attempts": MAX_PREEXECUTION_FAILURES
                            })

                            if proposed_plan is not None:
                                rejected_plan_summary = self._summarize_plan(proposed_plan)
                            else:
                                rejected_plan_summary = "Aucun plan généré (erreur de validation JSON)"

                            feedback_msg = (
                                _("\n[⚠️ REJET DU PLAN PRÉCÉDENT]\n")
                                + _("Le système a rejeté votre plan pour l'erreur suivante :\n{}\n").format(error_msg)
                                + _("Voici la structure de votre plan rejeté pour vous aider à identifier l'erreur :\n")
                                + rejected_plan_summary
                                + _("\nVeuillez corriger cette erreur dans votre nouveau plan.")
                            )
                            self.context = f"{self.context}\n{feedback_msg}" if self.context else feedback_msg
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue
                        except ValueError as plan_error:
                            self.runtime_state.update_marker("plan_rejected", True)
                            error_msg = str(plan_error)
                            Logger.warning(f"[Solver:{self.id}] ⚠️ Plan invalide (Validation personnalisée) : {plan_error}")
                            if hasattr(self, 'planner') and hasattr(self.planner, '_last_proposed_plan'):
                                Logger.debug(f"[Solver:{self.id}] Plan rejeté : {self.planner._last_proposed_plan}")
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_VALIDATION
                            self.current_attempt.failure_reason = str(plan_error)
                            self.current_attempt.planner_feedback = str(plan_error)
                            self.current_attempt.target_entity = "Planner"

                            await self.propagate_event(Events.PLANNER_RETRY, {
                                "reason": str(plan_error),
                                "attempt": self._preexecution_failures + 1,
                                "max_attempts": MAX_PREEXECUTION_FAILURES
                            })

                            if proposed_plan is not None:
                                rejected_plan_summary = self._summarize_plan(proposed_plan)
                            else:
                                rejected_plan_summary = "Aucun plan généré (erreur de validation)"
                            feedback_msg = (
                                _("\n[⚠️ REJET DU PLAN PRÉCÉDENT]\n")
                                + _("Le système a rejeté votre plan pour l'erreur suivante :\n{}\n").format(error_msg)
                                + _("Voici la structure de votre plan rejeté pour vous aider à identifier l'erreur :\n")
                                + rejected_plan_summary
                                + _("\nVeuillez corriger cette erreur dans votre nouveau plan.")
                            )
                            self.context = f"{self.context}\n{feedback_msg}" if self.context else feedback_msg
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue

                        if self.runtime_state.cancel_requested:
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.USER_CANCELLED
                            self.current_attempt.failure_reason = _("Annulation demandée")
                            break

                        previous_attempts = self.execution_tree.attempts[:-1] if self.execution_tree else []
                        validation_outcome = await self.validate_plan(
                            proposed_plan, self.id, previous_attempts=previous_attempts
                        )
                        is_valid = bool(validation_outcome)
                        rejection_reason = getattr(
                            validation_outcome, "reason", _("Plan refusé par le superviseur")
                        )
                        if not is_valid:
                            self.runtime_state.update_marker("plan_rejected", True)
                            Logger.warning(f"[Solver:{self.id}] Plan refusé par le superviseur : {rejection_reason}")
                            Logger.event(
                                "plan_rejected_supervisor",
                                solver_id=self.id,
                                attempt=attempt_counter,
                                reason=rejection_reason
                            )
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_SUPERVISOR
                            self.current_attempt.failure_reason = rejection_reason
                            self.current_attempt.target_entity = "Validator"
                            self.context += _("\n[Échec] Plan refusé par le Superviseur : {reason}").format(reason=rejection_reason)
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue

                        for step in proposed_plan.steps:
                            step.id = make_step_id(step.id)

                        plan_payload = {
                            "mission_id": self.id,
                            "goal": self.goal,
                            "parent_step_id": self.parent_step_id or "",
                            "steps": [
                                {
                                    "id": step.id,
                                    "description": step.description,
                                    "type": step.type.value,
                                    "status": ExecutionStatus.PENDING,
                                    "tool_name": getattr(step, 'tool_name', None),
                                    "step_context": step.step_context or ""
                                } for step in proposed_plan.steps
                            ]
                        }
                        await self.propagate_event(Events.PLAN_GENERATED, plan_payload)

                        with self.runtime_state.execution_context.scope(entity_name="Executor", entity_role="Solver"):
                            result = await self.executor.execute_plan(
                                plan=proposed_plan,
                                current_context=self.context,
                                current_attempt=self.current_attempt
                            )

                        self.context = result.final_context
                        self.current_attempt.ended_at = time.time()

                        if result.status == ExecutionStatus.SUCCESS:
                            self.current_attempt.outcome = "success"
                            self.current_attempt.failure_class = FailureClass.NONE
                            success = True
                            final_result = result
                            break
                        else:
                            self.current_attempt.outcome = "failed"
                            if result.failure_class:
                                self.current_attempt.failure_class = result.failure_class
                            else:
                                self.current_attempt.failure_class = FailureClass.EXECUTION_FAILURE
                            self.current_attempt.failure_reason = result.error_reason or _("Échec d'exécution")
                            self.current_attempt.target_entity = result.target_entity or "Executor"
                            Logger.warning(f"[Solver:{self.id}] 🔄 Échec exécution (Tentative {execution_attempt}/{MAX_EXECUTION_TRIES}). Raison : {result.error_reason}")
                            self.context += _("\n[Raison de l'échec] {}. Vous devez adapter le prochain plan.").format(result.error_reason)

                            await self.propagate_event(Events.PLAN_ABANDONED, {
                                "mission_id": self.id,
                                "parent_step_id": self.parent_step_id or "",
                                "reason": _("Échec (Tentative {}/{}).").format(execution_attempt, MAX_EXECUTION_TRIES)
                            })

                self.execution_tree.ended_at = time.time()

                if success and final_result is not None:
                    self.execution_tree.status = "success"

                    if self.signatures:
                        is_novel = self.runtime_state.execution_markers.get("is_novel", False)
                        should_store = False
                        if self._similar_missions is None:
                            should_store = True
                        else:
                            should_store = is_novel
                        if should_store:
                            asyncio.create_task(self._store_mission_profiles_async())
                        else:
                            Logger.debug(f"[Solver:{self.id}] Stockage MissionProfiles ignoré (mission non nouvelle).")

                    final_result.execution_tree = self.execution_tree
                    return final_result

                self.execution_tree.status = "failed"
                if self.current_attempt and self.current_attempt.ended_at is None:
                    self.current_attempt.ended_at = time.time()
                    self.current_attempt.outcome = "failed"
                    if self.runtime_state.cancel_requested:
                        self.current_attempt.failure_class = FailureClass.USER_CANCELLED
                        self.current_attempt.failure_reason = _("Annulation demandée")
                    else:
                        self.current_attempt.failure_class = FailureClass.MAX_RETRIES_REACHED
                        self.current_attempt.failure_reason = _("Échec définitif : impossible d'accomplir la tâche après plusieurs tentatives.")

                error_msg = self.current_attempt.failure_reason if self.current_attempt else _("Échec inconnu")
                result = SolverResult(
                    status=ExecutionStatus.FAILED,
                    final_context=self.context,
                    error_reason=error_msg,
                    execution_tree=self.execution_tree
                )
                return result

            except Exception as general_error:
                Logger.error(_("[Solver:{}] 🔥 Erreur critique inattendue : {}").format(self.id, str(general_error)))
                self.execution_tree.ended_at = time.time()
                self.execution_tree.status = "failed"
                if self.current_attempt and self.current_attempt.ended_at is None:
                    self.current_attempt.ended_at = time.time()
                    self.current_attempt.outcome = "failed"
                    self.current_attempt.failure_class = FailureClass.EXECUTION_FAILURE
                    self.current_attempt.failure_reason = str(general_error)
                raise general_error

            finally:
                # --- OBSERVABILITÉ : Émettre le registre du solver ---
                try:
                    registry_metadata_view = self._get_registry_metadata_view()
                    Logger.event(
                        "solver_registry",
                        solver_id=self.id,
                        registry=registry_metadata_view,
                        attempt_number=self.current_attempt.attempt_number if self.current_attempt else None,
                        goal=self.goal,
                        status=self.execution_tree.status if self.execution_tree else "unknown"
                    )
                except Exception as e:
                    Logger.warning(f"[Solver:{self.id}] Échec de l'émission du registre : {e}")

                if self.id in self.runtime_state.solver_registry:
                    del self.runtime_state.solver_registry[self.id]
                    Logger.debug(f"[Solver:{self.id}] Entrée supprimée du registre.")

    def _summarize_plan(self, plan: Plan) -> str:
        """Génère un résumé concis d'un plan pour le feedback."""
        lines = ["Structure du plan rejeté :"]
        for step in plan.steps:
            lines.append(
                f"  - {step.id}: type={step.type.value}, "
                f"output_var={step.output_variable_name or 'None'}, "
                f"expected={step.expected_result}, "
                f"tool={getattr(step, 'tool_name', 'None')}"
            )
        used_vars = set()
        for step in plan.steps:
            for field in [step.execute_if, step.response_text, step.tool_args_json]:
                if field:
                    matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', field)
                    used_vars.update(matches)
        lines.append(f"Variables utilisées : {', '.join(used_vars) if used_vars else 'aucune'}")
        return "\n".join(lines)

    def _format_similar_missions(self, similar: List[Dict]) -> str:
        lines = []
        limit = RETRIEVAL_MAX_RESULTS_INJECTED
        for idx, sm in enumerate(similar[:limit], 1):
            lines.append(f"{idx}. Mission : {sm.get('goal', '')}")
            lines.append(f"   Résumé : {sm.get('summary', '')}")
            lines.append(f"   Score : {sm.get('score', 0):.2f}")
        return "\n".join(lines)

    async def _check_feasibility(self, similar_missions_context: Optional[List[Dict]] = None) -> FeasibilityDecision:
        Logger.info(f"[Solver:{self.id}] 🤔 Évaluation de la faisabilité...")

        tools_view = await self.runtime_state.tools_manager.get_tools_view(goal_query=self.goal)
        formatted_tools = [f"- {t['name']} ({t['role']}): {t['description']}" for t in tools_view]

        registry_view = self._get_registry_metadata_view()
        registry_text = self._format_registry_metadata(registry_view)

        loader = get_prompt_loader()
        if hasattr(self, '_cached_advice') and self._cached_advice is not None:
            advice = self._cached_advice
        else:
            advice = ""
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    advice = await self.runtime_state.learner.get_advice(
                        entity_types=["Solver"], goal=self.goal
                    )
                    self._cached_advice = advice
                except Exception as e:
                    Logger.error(f"[Solver:{self.id}] Erreur récupération des conseils : {e}")

        prompt = loader.load(
            "feasibility.md",
            lang=self.runtime_state.language,
            goal=self.goal,
            context=self.context,
            tools="\n".join(formatted_tools),
            similar_missions=similar_missions_context,
            registry=registry_text,
            advice=advice
        )

        decision: FeasibilityDecision = await self.llm.generate_structured(
            prompt=prompt,
            schema=FeasibilityDecision,
            tag="FeasibilityDecision",
            mission_id=self.id
        )
        return decision

    async def _store_mission_profiles_async(self):
        if not self.signatures:
            return
        try:
            embedding_manager = self.runtime_state.embedding_manager
            if not embedding_manager or not embedding_manager.active_provider:
                Logger.warning(f"[Solver:{self.id}] Aucun provider d'embedding actif, stockage ignoré.")
                return
            store = MissionProfileStore()
            active_model = embedding_manager.active_provider.model_name
            dimension = embedding_manager.dimension
            root_mission_id = self.runtime_state.current_mission_id or self.id

            for idx, sig in enumerate(self.signatures):
                signature_text = f"{sig.action} {sig.object}"
                embedding = await embedding_manager.embed(signature_text)
                store.insert_profile(
                    mission_id=self.id,
                    signature_text=signature_text,
                    embedding=embedding,
                    action=sig.action,
                    object=sig.object,
                    desired_state=sig.desired_state,
                    signature_index=idx,
                    signature_count=len(self.signatures),
                    embedding_model=active_model,
                    embedding_dimension=dimension,
                    root_mission_id=root_mission_id
                )
            Logger.info(f"[Solver:{self.id}] ✅ {len(self.signatures)} embedding(s) stocké(s) (root={root_mission_id}).")
        except Exception as e:
            Logger.warning(f"[Solver:{self.id}] Échec stockage embeddings : {e}")

    async def validate_plan(
        self, plan: Plan, child_solver_id: str, previous_attempts: Optional[List] = None
    ):
        root_solver = self._get_root_solver()
        mission_history_tree = root_solver.execution_tree if root_solver else self.execution_tree
        try:
            return await self.parent.validate_plan(
                plan, child_solver_id, previous_attempts=previous_attempts,
                mission_history_tree=mission_history_tree,
            )
        except TypeError as e:
            if "mission_history_tree" not in str(e):
                raise
            Logger.error(
                f"[Solver:{self.id}] validate_plan : incompatibilité de version détectée "
                f"({e}) — repli sur l'appel sans mission_history_tree. Vérifier que "
                f"orchestrator.py/solver.py/plan_validator.py sont bien synchronisés."
            )
            return await self.parent.validate_plan(
                plan, child_solver_id, previous_attempts=previous_attempts
            )

    def _get_root_solver(self) -> 'Solver':
        node = self
        while hasattr(node, "goal") and hasattr(node, "depth") and hasattr(node, "id"):
            parent = getattr(node, "parent", None)
            if parent is None or not (hasattr(parent, "goal") and hasattr(parent, "depth") and hasattr(parent, "id")):
                return node
            node = parent
        return self

    def _build_ancestor_chain(self) -> List[Dict[str, Any]]:
        chain = []
        node = self
        while hasattr(node, "goal") and hasattr(node, "depth") and hasattr(node, "id"):
            chain.append({"depth": node.depth, "goal": node.goal, "solver_id": node.id})
            node = getattr(node, "parent", None)
            if node is None:
                break
        chain.reverse()
        return chain

    async def request_depth_extension(self, ancestor_chain: List[Dict[str, Any]], child_solver_id: str) -> bool:
        try:
            return await self.parent.request_depth_extension(ancestor_chain, child_solver_id)
        except TypeError as e:
            Logger.error(
                f"[Solver:{self.id}] request_depth_extension : incompatibilité de version "
                f"détectée ({e}) — extension refusée par prudence plutôt que de planter la mission."
            )
            return False

    def _format_registry_metadata(self, registry_view: dict) -> str:
        """Formate la vue des métadonnées du registre pour l'affichage dans un prompt."""
        if not registry_view:
            return _("(registre vide)")
        lines = []
        for key, meta in registry_view.items():
            lines.append(f"- **`$@_{key}`** : {meta.get('description', '')}")
            lines.append(f"  - Type : {meta.get('type', 'unknown')} | Source : {meta.get('source', 'N/A')}")
            lines.append(f"  - Hint : {meta.get('value_hint', '')}")
        return "\n".join(lines)

    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        await self.parent.report_critical_failure(error_context, child_solver_id)

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        return await self.runtime_state.tools_manager.execute_tool(
            tool_name, arguments, llm=self.llm
        )

    async def propagate_event(self, event_name: str, payload: dict):
        await self.parent.propagate_event(event_name, payload)

    async def process(self, *args, **kwargs) -> Any:
        return await self.run()

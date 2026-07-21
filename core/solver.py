from typing import Optional, Any, Dict, List
from utils.logger import Logger
from core.entity import Entity
from core.constants import Events
from .supervisor import Supervisor
from .plan_models import FeasibilityDecision, Plan, SolverResult, ExecutionStatus, MissionSignature
from .planner import Planner
from .executor import Executor
from core.llm import Llm
from pydantic import ValidationError   # <-- NOUVEAU
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.id_generator import make_step_id
import time
from core.execution_models import ExecutionTree, PlanAttempt, FailureClass
from core.retriever import Retriever
from core.signature_extractor import SignatureExtractor
from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD, RETRIEVAL_MAX_RESULTS_INJECTED

MAX_DEPTH = 10
MAX_EXECUTION_TRIES = 3
MAX_PREEXECUTION_FAILURES = 3


class Solver(Supervisor, Entity):
    
    def __init__(self, solver_id: str, goal: str, parent: Supervisor, provider_manager, runtime_state,
                 provider_id: str = None, model_id: str = None, llm: Optional[Llm] = None, 
                 depth: int = 0, context: str = "", parent_step_id: str = None):
        
        Supervisor.__init__(self)
        Entity.__init__(self, name=solver_id, role="solver", llm=llm, parent=parent)

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

        if not self.llm and provider_id and model_id:
            self.llm = Llm(
                provider_manager=provider_manager,
                provider_id=provider_id,
                model_id=model_id
            )

        if not self.llm:
            raise ValueError(_("Solver requires a Llm instance or provider/model identifiers."))

        if isinstance(parent, Solver):
            self.variable_registry = dict(parent.variable_registry)
        else:
            self.variable_registry = {}

        self.planner = Planner(llm=self.llm, runtime_state=self.runtime_state)
        self.executor = Executor(solver_node=self)

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
            parent_solver_id=self.parent.id if hasattr(self.parent, 'id') else None
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

            try:
                if not self.signatures:
                    try:
                        extractor = SignatureExtractor(llm=self.llm, runtime_state=self.runtime_state)
                        signatures = await extractor.extract(self.goal, self.context)
                        self.assign_signatures(signatures)
                        Logger.info(f"[Solver:{self.id}] {len(signatures)} signature(s) générée(s).")
                    except Exception as e:
                        Logger.error(f"[Solver:{self.id}] Échec de SignatureExtractor : {e}")

                similar_missions_context = ""
                if self.signatures:
                    registry_entry = self.runtime_state.solver_registry.get(self.id)
                    if registry_entry and registry_entry.get("similar_missions") is not None:
                        similar = registry_entry["similar_missions"]
                        if similar:
                            similar_missions_context = similar
                            Logger.info(f"[Solver:{self.id}] Cache utilisé : {len(similar)} missions.")
                    else:
                        try:
                            retriever = Retriever(
                                runtime_state=self.runtime_state,
                                top_k=RETRIEVAL_TOP_K,
                                threshold=RETRIEVAL_THRESHOLD
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
                            if similar:
                                similar_missions_context = similar
                            else:
                                similar_missions_context = None
                        except Exception as e:
                            Logger.error(f"[Solver:{self.id}] Échec du retrieval : {e}")

                # Feasibility
                with self.runtime_state.execution_context.scope(entity_name="Feasibility", entity_role="Solver"):
                    decision = await self._check_feasibility(similar_missions_context)

                if not decision.is_possible:
                    Logger.warning(f"[Solver:{self.id}] 🛑 Objectif impossible. Raison : {decision.reason}")
                    self.execution_tree.ended_at = time.time()
                    self.execution_tree.status = "failed"
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=self.context,
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
                        
                        # --- NOUVEAU : capture explicite de ValidationError ---
                        except ValidationError as pydantic_error:
                            error_msg = f"Erreur de validation Pydantic : {pydantic_error}"
                            Logger.warning(f"[Solver:{self.id}] ⚠️ Plan invalide (Pydantic) : {error_msg}")
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

                            feedback_msg = (
                                _("\n[⚠️ REJET DU PLAN PRÉCÉDENT]\n") +
                                _("Le système a rejeté votre plan pour l'erreur suivante :\n{}\n").format(error_msg) +
                                _("Veuillez corriger cette erreur de logique/syntaxe dans votre nouveau plan.")
                            )
                            self.context = f"{self.context}\n{feedback_msg}" if self.context else feedback_msg
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue

                        except ValueError as plan_error:
                            Logger.warning(f"[Solver:{self.id}] ⚠️ Plan invalide (Validation personnalisée) : {plan_error}")
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

                            feedback_msg = (
                                _("\n[⚠️ REJET DU PLAN PRÉCÉDENT]\n") +
                                _("Le système d'exécution a rejeté votre plan pour l'erreur suivante :\n{}\n").format(plan_error) +
                                _("Veuillez corriger cette erreur de logique/syntaxe dans votre nouveau plan.")
                            )
                            self.context = f"{self.context}\n{feedback_msg}" if self.context else feedback_msg
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue

                        # Suite du code inchangée (validation, exécution, etc.)
                        if self.runtime_state.cancel_requested:
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.USER_CANCELLED
                            self.current_attempt.failure_reason = _("Annulation demandée")
                            break

                        is_valid = await self.parent.validate_plan(proposed_plan, self.id)
                        if not is_valid:
                            Logger.warning(f"[Solver:{self.id}] Plan refusé par le superviseur.")
                            self.current_attempt.ended_at = time.time()
                            self.current_attempt.outcome = "failed"
                            self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_SUPERVISOR
                            self.current_attempt.failure_reason = _("Plan refusé par le superviseur")
                            self.current_attempt.target_entity = "Orchestrator"
                            self.context += _("\n[Échec] Plan refusé par le Superviseur.")
                            self._preexecution_failures += 1
                            if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                                break
                            continue

                        execution_attempt += 1

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
                if self.id in self.runtime_state.solver_registry:
                    del self.runtime_state.solver_registry[self.id]
                    Logger.debug(f"[Solver:{self.id}] Entrée supprimée du registre.")

    
    # =====================================================
    # MÉTHODES UTILITAIRES
    # =====================================================
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
        loader = get_prompt_loader()
        prompt = loader.load(
            "feasibility.md",
            lang=self.runtime_state.language,
            goal=self.goal,
            context=self.context,
            tools="\n".join(formatted_tools),
            similar_missions=similar_missions_context
        )
        decision: FeasibilityDecision = await self.llm.generate_structured(
            prompt=prompt,
            schema=FeasibilityDecision
        )
        return decision

    async def validate_plan(self, plan: Plan, child_solver_id: str) -> bool:
        return await self.parent.validate_plan(plan, child_solver_id)

    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        await self.parent.report_critical_failure(error_context, child_solver_id)

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        return await self.parent.execute_tool(tool_name, arguments)

    async def propagate_event(self, event_name: str, payload: dict):
        await self.parent.propagate_event(event_name, payload)

    async def process(self, *args, **kwargs) -> Any:
        return await self.run()
from typing import Optional, Any
from utils.logger import Logger
from core.entity import Entity
from core.constants import Events
from .supervisor import Supervisor
from .plan_models import FeasibilityDecision, Plan, SolverResult, ExecutionStatus
from .planner import Planner
from .executor import Executor
from core.llm import Llm
from pydantic import ValidationError
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.id_generator import make_step_id
import time
from core.execution_models import ExecutionTree, PlanAttempt, FailureClass

MAX_DEPTH = 10

# --- 1.4 : Budgets de tentatives DÉCOUPLÉS ---
# Avant ce correctif, un rejet de plan (validation statique OU superviseur) consommait le MÊME
# slot que les échecs d'EXÉCUTION réelle (max_tries=3 pour les deux confondus) — alors qu'un
# rejet de plan ne coûte qu'un appel LLM, rien n'a touché le monde réel. Résultat observé en
# test : une mission pouvait épuiser son budget sur 2 plans invalides + 1 vraie tentative,
# quand une autre l'épuisait sur 3 vraies tentatives — même "3 essais" en apparence, expérience
# très différente. Les deux budgets sont maintenant indépendants.
MAX_EXECUTION_TRIES = 3        # Vraies tentatives (le plan a été validé ET dispatché à l'Executor)
MAX_PREEXECUTION_FAILURES = 3  # Plans rejetés AVANT toute exécution (validation ou superviseur)

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
        self._preexecution_failures = 0  # Renommé (ex _validation_failures) : couvre aussi les rejets superviseur
        self.execution_tree = None  # sera créé dans run()
        self.current_attempt = None  # sera défini dans run()

        if not self.llm and provider_id and model_id:
            self.llm = Llm(
                provider_manager=provider_manager,
                provider_id=provider_id,
                model_id=model_id
            )

        if not self.llm:
            raise ValueError(_("Solver requires a Llm instance or provider/model identifiers."))

        # ---> NOUVEAU : Isolation du Registre (Bac à sable) <---
        if isinstance(parent, Solver):
            # Le child hérite des variables existantes (lecture) mais aura son propre espace local
            self.variable_registry = dict(parent.variable_registry)
        else:
            self.variable_registry = {}

        self.planner = Planner(llm=self.llm, runtime_state=self.runtime_state)
        self.executor = Executor(solver_node=self)

    # =====================================================
    # BOUCLE CENTRALE D'INFÉRENCE (Think -> Plan -> Execute)
    # =====================================================
    # =====================================================
    # BOUCLE CENTRALE D'INFÉRENCE (Think -> Plan -> Execute)
    # =====================================================
    async def run(self) -> SolverResult:
        if self.runtime_state.cancel_requested:
            return SolverResult(
                status=ExecutionStatus.FAILED,
                final_context=self.context,
                error_reason=_("Génération annulée")
            )

        # Initialisation de l'arbre d'exécution pour ce solver
        self.execution_tree = ExecutionTree(
            solver_id=self.id,
            goal=self.goal,
            parent_solver_id=self.parent.id if hasattr(self.parent, 'id') else None,
            parent_step_id=self.parent_step_id,
            depth=self.depth,
            started_at=time.time(),
            status="failed"  # sera mis à jour
        )

        try:
            # 1. PHASE DE RÉFLEXION (Feasibility Check)
            decision = await self._check_feasibility()

            if not decision.is_possible:
                Logger.warning(f"[Solver:{self.id}] 🛑 Objectif impossible. Raison générée pour l'utilisateur : {decision.reason}")
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
            execution_attempt = 0  # Budget "réel" : n'avance QUE quand un plan validé est dispatché
            attempt_counter = 0    # Purement pour la numérotation télémétrique (jamais une condition de sortie)

            while execution_attempt < MAX_EXECUTION_TRIES:
                if self.runtime_state.cancel_requested:
                    break

                # --- CRÉATION DE LA TENTATIVE COURANTE (télémétrie) ---
                attempt_counter += 1
                self.current_attempt = PlanAttempt(
                    attempt_number=attempt_counter,
                    started_at=time.time(),
                    outcome="failed",
                    failure_class=FailureClass.NONE
                )
                self.execution_tree.add_attempt(self.current_attempt)

                # --- PLANIFICATION ---
                try:
                    proposed_plan = await self.planner.propose_plan(
                        goal=self.goal,
                        context=self.context,
                        strategy=decision.refined_strategy,
                        variable_registry=self.variable_registry
                    )
                    # Stocker le plan sérialisé
                    self.current_attempt.proposed_plan = proposed_plan.model_dump(mode='json')
                except ValueError as plan_error:
                    Logger.warning(f"[Solver:{self.id}] ⚠️ Plan invalide : {plan_error}")
                    self.current_attempt.ended_at = time.time()
                    self.current_attempt.outcome = "failed"
                    self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_VALIDATION
                    self.current_attempt.failure_reason = str(plan_error)
                    self.current_attempt.planner_feedback = str(plan_error)
                    # Blâme certain : c'est Planner._validate_plan() qui a rejeté, sans ambiguïté possible.
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

                    # --- 1.4 : ne consomme PAS execution_attempt — rien n'a touché le monde réel ---
                    self._preexecution_failures += 1
                    if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                        break
                    continue

                # --- CHECKPOINT ANNULATION ---
                if self.runtime_state.cancel_requested:
                    self.current_attempt.ended_at = time.time()
                    self.current_attempt.outcome = "failed"
                    self.current_attempt.failure_class = FailureClass.USER_CANCELLED
                    self.current_attempt.failure_reason = _("Annulation demandée")
                    break

                # --- VALIDATION PAR LE SUPERVISEUR ---
                is_valid = await self.parent.validate_plan(proposed_plan, self.id)
                if not is_valid:
                    Logger.warning(f"[Solver:{self.id}] Plan refusé par le superviseur.")
                    self.current_attempt.ended_at = time.time()
                    self.current_attempt.outcome = "failed"
                    self.current_attempt.failure_class = FailureClass.PLAN_REJECTED_SUPERVISOR
                    self.current_attempt.failure_reason = _("Plan refusé par le superviseur")
                    # Blâme certain : Solver.validate_plan() n'est qu'un proxy qui relaie vers le parent ;
                    # au bout de la chaîne récursive, c'est toujours l'Orchestrateur qui juge réellement.
                    self.current_attempt.target_entity = "Orchestrator"
                    self.context += _("\n[Échec] Plan refusé par le Superviseur.")

                    # --- 1.4 : même budget que le rejet de validation, même raison (rien d'exécuté) ---
                    self._preexecution_failures += 1
                    if self._preexecution_failures >= MAX_PREEXECUTION_FAILURES:
                        break
                    continue

                # --- À PARTIR D'ICI : le plan est validé, on va réellement l'exécuter ---
                # --- 1.4 : SEUL point d'incrémentation du budget d'exécution réelle ---
                execution_attempt += 1

                # --- SÉCURISATION DES IDs ---
                for step in proposed_plan.steps:
                    step.id = make_step_id(step.id)

                # --- ÉMISSION DU PLAN VERS LE C++ ---
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

                # --- EXÉCUTION DU PLAN ---
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
                    # --- NOUVEAU : propagation de la failure_class depuis l'Executor ---
                    if result.failure_class:
                        self.current_attempt.failure_class = result.failure_class
                    else:
                        # Fallback de sécurité (normalement l'Executor remplit toujours ce champ)
                        self.current_attempt.failure_class = FailureClass.EXECUTION_FAILURE
                        Logger.warning(f"[Solver:{self.id}] failure_class None, fallback EXECUTION_FAILURE")

                    self.current_attempt.failure_reason = result.error_reason or _("Échec d'exécution")
                    # --- NOUVEAU : propagation de target_entity depuis l'Executor (même logique que failure_class) ---
                    self.current_attempt.target_entity = result.target_entity or "Executor"
                    Logger.warning(f"[Solver:{self.id}] 🔄 Échec exécution (Tentative {execution_attempt}/{MAX_EXECUTION_TRIES}). Raison : {result.error_reason}")
                    self.context += _("\n[Raison de l'échec] {}. Vous devez adapter le prochain plan.").format(result.error_reason)

                    await self.propagate_event(Events.PLAN_ABANDONED, {
                        "mission_id": self.id,
                        "parent_step_id": self.parent_step_id or "",
                        "reason": _("Échec (Tentative {}/{}).").format(execution_attempt, MAX_EXECUTION_TRIES)
                    })

            # --- FIN DE LA BOUCLE ---
            self.execution_tree.ended_at = time.time()

            if success and final_result is not None:
                self.execution_tree.status = "success"
                final_result.execution_tree = self.execution_tree
                return final_result

            # Échec global
            self.execution_tree.status = "failed"
            if self.current_attempt and self.current_attempt.ended_at is None:
                self.current_attempt.ended_at = time.time()
                self.current_attempt.outcome = "failed"
                if self.runtime_state.cancel_requested:
                    self.current_attempt.failure_class = FailureClass.USER_CANCELLED
                    self.current_attempt.failure_reason = _("Annulation demandée")
                else:
                    # Épuisement des tentatives
                    self.current_attempt.failure_class = FailureClass.MAX_RETRIES_REACHED
                    self.current_attempt.failure_reason = _("Échec définitif : impossible d'accomplir la tâche après plusieurs tentatives.")
                    # target_entity volontairement laissé à None ici : ce chemin de sortie est rare et on ne
                    # reconstruit pas un blâme a posteriori (voir la règle générale plus haut). L'Analyzer doit
                    # ignorer la génération de leçon "entité" pour un attempt sans target_entity, pas deviner.

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
            
    # =====================================================
    # MÉTHODES INTERNES
    # =====================================================
    # async def _check_feasibility(self) -> FeasibilityDecision:
    #     """Utilise le LLM pour vérifier si l'objectif est atteignable avant de planifier."""
    #     Logger.info(f"[Solver:{self.id}] 🤔 Évaluation de la faisabilité du but...")
        
    #     tool_names = [t.get('name') for t in self.runtime_state.available_tools]
        
    #     prompt = f"""
    #     Tu es le module d'évaluation stratégique principal du système. Analyse cette requête.
        
    #     BUT À ATTEINDRE : {self.goal}
    #     CONTEXTE D'EXÉCUTION : {self.context}
    #     OUTILS DISPONIBLES : {tool_names}
        
    #     Détermine si tu disposes des outils matériels nécessaires pour accomplir ce but.
    #     Si OUI : Rédige dans 'refined_strategy' une stratégie courte des étapes logiques à suivre.
    #     Si NON : Dans 'reason', rédige une explication polie ET DIRECTEMENT ADRESSÉE À L'UTILISATEUR pour lui expliquer pourquoi sa demande ne peut pas être exécutée.
    #     """
        
    #     # Le bloc try/except est retiré. Si le LLM crash ou timeout, 
    #     # l'exception remontera purement jusqu'à l'Orchestrateur.
    #     decision: FeasibilityDecision = await self.llm.generate_structured(
    #         prompt=prompt,
    #         schema=FeasibilityDecision
    #     )
    #     return decision

    # async def _check_feasibility(self) -> FeasibilityDecision:
    #     Logger.info(f"[Solver:{self.id}] 🤔 Évaluation de la faisabilité du but...")
        
    #     loader = get_prompt_loader()
    #     prompt = loader.load(
    #         "feasibility.md",
    #         lang=self.runtime_state.language,
    #         goal=self.goal,
    #         context=self.context,
    #         tools=[t.get('name') for t in self.runtime_state.available_tools]
    #     )
        
    #     decision: FeasibilityDecision = await self.llm.generate_structured(
    #         prompt=prompt,
    #         schema=FeasibilityDecision
    #     )
    #     return decision
        
    async def _check_feasibility(self) -> FeasibilityDecision:
        Logger.info(f"[Solver:{self.id}] 🤔 Évaluation de la faisabilité du but...")
        
        # ---> NOUVEAU : On demande une "vue" explicitement au ToolsManager
        # On passe le 'goal' en prévision du futur filtrage LLM
        tools_view = await self.runtime_state.tools_manager.get_tools_view(goal_query=self.goal)
        
        # On formate cette vue textuellement pour le prompt (nom, rôle et description)
        formatted_tools = [
            f"- {t['name']} ({t['role']}): {t['description']}" for t in tools_view
        ]
        
        loader = get_prompt_loader()
        # Le prompt 'feasibility.md' recevra désormais des descriptions riches, pas juste des noms
        prompt = loader.load(
            "feasibility.md",
            lang=self.runtime_state.language,
            goal=self.goal,
            context=self.context,
            tools="\n".join(formatted_tools) 
        )
        
        decision: FeasibilityDecision = await self.llm.generate_structured(
            prompt=prompt,
            schema=FeasibilityDecision
        )
        return decision
    # =====================================================
    # IMPLÉMENTATION DE SUPERVISOR (Escalade vers l'Orchestrator)
    # =====================================================
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
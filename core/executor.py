import asyncio
import json
from typing import List, Optional, Tuple
from .plan_models import Plan, PlanStep, SolverResult, ExecutionStatus, StepType, ConvergenceDecision
from core.constants import Events
from utils.logger import Logger
import re
from datetime import datetime
from core.i18n import _
from core.prompt_loader import get_prompt_loader
from core.execution_models import (
    ExecutionNode,
    PlanAttempt,
    FailureClass
)
import time

class Executor:
    def __init__(self, solver_node):
        self.solver = solver_node

    def _step_event_meta(self, node: 'ExecutionNode') -> dict:
        """
        Métadonnées communes à injecter dans chaque événement STEP_STATUS_CHANGED
        pour que le frontend puisse afficher les arguments d'outil et les
        horodatages dans le panneau de détails d'une étape. Ces informations
        existent déjà intégralement sur `node` (ExecutionNode) mais n'étaient
        jusqu'ici jamais transmises au-delà du backend.
        """
        meta = {
            "started_at": datetime.fromtimestamp(node.started_at).isoformat() if node.started_at else None,
            "ended_at": datetime.fromtimestamp(node.ended_at).isoformat() if getattr(node, "ended_at", None) else None,
        }
        if node.tool_name:
            meta["tool_name"] = node.tool_name
        if node.tool_args:
            meta["tool_args"] = node.tool_args
        return meta

    async def execute_plan(self, plan: Plan, current_context: str, current_attempt: PlanAttempt) -> SolverResult:
        Logger.info(f"[Executor] 🚀 Initialisation de l'exécution du plan : '{plan.goal}'")

        user_responses: List[str] = []
        executed_steps_trace: List[str] = []
        accumulated_context = current_context

        if not hasattr(self.solver.runtime_state, 'mission_rum'):
            self.solver.runtime_state.mission_rum = {}

        try:
            for step in plan.steps:
                if self.solver.runtime_state.cancel_requested:
                    Logger.warning(f"[Executor] 🛑 Interruption demandée à l'étape [{step.id}].")
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=accumulated_context,
                        error_reason=_("Exécution interrompue"),
                        failure_class=FailureClass.USER_CANCELLED
                    )

                with self.solver.runtime_state.execution_context.scope(step_id=step.id):
                    node = ExecutionNode(
                        step_id=step.id,
                        description=step.description,
                        step_type=step.type.value,
                        tool_name=step.tool_name if step.type == StepType.TOOL_CALL else None,
                        tool_args=step.get_parsed_args if step.type == StepType.TOOL_CALL else None,
                        expected_result=step.expected_result,
                        status=ExecutionStatus.PENDING,
                        started_at=time.time()
                    )
                    current_attempt.add_node(node)

                    if step.execute_if:
                        condition_evaluation = self._evaluate_condition(step.execute_if)
                        if not condition_evaluation:
                            Logger.info(f"[Executor] ⏭️ Étape [{step.id}] SAUTÉE.")
                            step.status = ExecutionStatus.SKIPPED
                            node.status = ExecutionStatus.SKIPPED
                            node.actual_result = _("Étape ignorée par branchement conditionnel : {}").format(step.execute_if)
                            node.ended_at = time.time()

                            await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                                "step_id": step.id,
                                "status": ExecutionStatus.SKIPPED,
                                "reason": _("Condition ({}) non remplie").format(step.execute_if),
                                **self._step_event_meta(node)
                            })

                            step.result_context = _("Étape ignorée par branchement conditionnel : {}").format(step.execute_if)

                            # --- TRACE SAUTÉE (ENRICHIE, SANS TRONCATURE) ---
                            tool_info = f" [{step.tool_name}]" if step.type == StepType.TOOL_CALL and step.tool_name else ""
                            executed_steps_trace.append(
                                _("- [{}] {} {} : Sautée (Condition non remplie)").format(
                                    step.id,
                                    step.description,
                                    step.type.value + tool_info
                                )
                            )

                            # Définir une variable de contrôle pour l'étape sautée (si output_variable_name est défini)
                            if step.output_variable_name:
                                self.solver.variable_registry[step.output_variable_name] = {
                                    "value": "false",
                                    "description": step.output_variable_desc or _("Statut de l'étape sautée {}").format(step.id),
                                    "source": self.solver.id,
                                    "timestamp": datetime.now().isoformat()
                                }
                                if step.is_crucial:
                                    self._propagate_crucial_variable(step.output_variable_name)

                            continue

                    Logger.info(f"[Executor] ⚙️ Traitement de l'étape [{step.id}] -> {step.description}")
                    await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                        "step_id": step.id,
                        "status": ExecutionStatus.RUNNING,
                        "description": step.description,
                        **self._step_event_meta(node)
                    })

                    if step.type == StepType.ABSTRACT_TASK:
                        success, execution_output, supplemental_data, child_result = await self._handle_abstract_task(step, accumulated_context)
                        if child_result and child_result.execution_tree:
                            node.child_execution_tree = child_result.execution_tree
                    else:
                        success, execution_output, supplemental_data = await self._execute_step_action(step, accumulated_context)

                    if step.type == StepType.TOOL_CALL and success and execution_output is not None:
                        node.raw_tool_success = str(execution_output).strip().lower() == "true"

                    if self.solver.runtime_state.cancel_requested:
                        Logger.warning(f"[Executor] 🛑 Interruption après action de l'étape [{step.id}].")
                        node.status = ExecutionStatus.FAILED
                        node.error_reason = _("Arrêté par l'utilisateur.")
                        node.ended_at = time.time()
                        return SolverResult(status=ExecutionStatus.FAILED, final_context=accumulated_context,
                                            error_reason=_("Arrêté par l'utilisateur."))

                    if not success:
                        Logger.error(f"[Executor] ❌ Échec à l'étape [{step.id}].")
                        step.status = ExecutionStatus.FAILED
                        step.result_context = supplemental_data or _("Échec d'exécution de l'action.")
                        node.status = ExecutionStatus.FAILED
                        node.actual_result = supplemental_data
                        node.error_reason = step.result_context
                        node.ended_at = time.time()

                        await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                            "step_id": step.id,
                            "status": ExecutionStatus.FAILED,
                            "reason": step.result_context,
                            **self._step_event_meta(node)
                        })

                        failure_trace = self._build_failure_trace(
                            executed_steps_trace, step, _("Échec d'exécution de l'action"), step.result_context
                        )
                        registry_meta = self.solver._get_registry_metadata_view()
                        final_context = accumulated_context + f"\n{failure_trace}\n\n--- Registre (métadonnées) ---\n{self._format_registry_view(registry_meta)}"
                        return SolverResult(
                            status=ExecutionStatus.FAILED,
                            final_context=final_context,
                            error_reason=_("Échec à l'étape {} : {}").format(step.id, step.result_context),
                            failure_class=FailureClass.EXECUTION_FAILURE,
                            target_entity="Executor"
                        )

                    convergence = await self._check_convergence(step, execution_output)

                    if self.solver.runtime_state.cancel_requested:
                        Logger.warning(f"[Executor] 🛑 Interruption après convergence de l'étape [{step.id}].")
                        node.status = ExecutionStatus.FAILED
                        node.error_reason = _("Generation stoppee.")
                        node.ended_at = time.time()
                        return SolverResult(status=ExecutionStatus.FAILED, final_context=accumulated_context,
                                            error_reason=_("Generation stoppee."),
                                            failure_class=FailureClass.USER_CANCELLED)

                    if convergence.is_convergent:
                        Logger.info(f"[Executor] ✅ Étape [{step.id}] validée.")
                        step.status = ExecutionStatus.SUCCESS
                        # Ne pas tronquer l'output pour le contexte final, seule l'affichage dans la trace est modifié
                        step.result_context = execution_output
                        node.status = ExecutionStatus.SUCCESS
                        node.actual_result = execution_output
                        node.ended_at = time.time()

                        await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                            "step_id": step.id,
                            "status": ExecutionStatus.SUCCESS,
                            "result": execution_output,
                            **self._step_event_meta(node)
                        })

                        # --- TRACE VALIDÉE (ENRICHIE, SANS TRONCATURE) ---
                        tool_info = f" [{step.tool_name}]" if step.type == StepType.TOOL_CALL and step.tool_name else ""
                        executed_steps_trace.append(
                            _("- [{}] {} {} : {}").format(
                                step.id,
                                step.description,
                                step.type.value + tool_info,
                                execution_output if execution_output else "succès"
                            )
                        )

                        accumulated_context += f"\n[Succès {step.id}] : {execution_output}"

                        if step.type in [StepType.DIRECT_ANSWER, StepType.ABSTRACT_TASK]:
                            if execution_output:
                                user_responses.append(execution_output)

                        if step.is_crucial and step.output_variable_name:
                            self._propagate_crucial_variable(step.output_variable_name)

                    else:
                        Logger.error(f"[Executor] ❌ Échec de convergence à l'étape [{step.id}].")
                        step.status = ExecutionStatus.FAILED
                        step.result_context = _("Divergence constatée : {}").format(convergence.reason)
                        node.status = ExecutionStatus.FAILED
                        node.actual_result = execution_output
                        node.error_reason = convergence.reason
                        node.ended_at = time.time()

                        await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                            "step_id": step.id,
                            "status": ExecutionStatus.FAILED,
                            "reason": convergence.reason,
                            **self._step_event_meta(node)
                        })

                        failure_trace = self._build_failure_trace(
                            executed_steps_trace, step, _("Divergence de résultat (Non-convergence)"), convergence.reason
                        )
                        registry_meta = self.solver._get_registry_metadata_view()
                        final_context = accumulated_context + f"\n{failure_trace}\n\n--- Registre (métadonnées) ---\n{self._format_registry_view(registry_meta)}"
                        return SolverResult(
                            status=ExecutionStatus.FAILED,
                            final_context=final_context,
                            error_reason=_("Divergence détectée à l'étape {} : {}").format(step.id, convergence.reason),
                            failure_class=FailureClass.CONVERGENCE_FAILURE,
                            target_entity="Executor"
                        )

            final_user_text = "\n\n".join([r for r in user_responses if r])
            final_user_text = self._interpolate_text(final_user_text, for_json=False)

            Logger.info("[Executor] 🎉 Fin de traitement : toutes les étapes ont convergé.")

            steps_summary = "\n".join(executed_steps_trace) if executed_steps_trace else _("Aucune étape exécutée.")
            registry_meta = self.solver._get_registry_metadata_view()
            registry_text = self._format_registry_view(registry_meta)

            final_context = (
                _("=== RÉSUMÉ DE L'EXÉCUTION ===\n")
                + steps_summary
                + _("\n\n--- REGISTRE (MÉTADONNÉES) ---\n")
                + registry_text
            )

            return SolverResult(
                status=ExecutionStatus.SUCCESS,
                final_context=final_context,
                response=final_user_text or _("Mission [{}] accomplie.").format(self.solver.id),
                resolved_data=self.solver.variable_registry,
                failure_class=None
            )

        except Exception as e:
            Logger.error(f"[Executor] 🔥 Exception critique : {str(e)}")
            raise e

    # =====================================================
    # PROPAGATION DES VARIABLES CRUCIALES
    # =====================================================

    def _propagate_crucial_variable(self, base_name: str) -> None:
        rum = self.solver.runtime_state.mission_rum
        if base_name in self.solver.variable_registry:
            rum[base_name] = self.solver.variable_registry[base_name]
            Logger.debug(f"[Executor] Variable cruciale '{base_name}' propagée vers le RUM.")

        if base_name.startswith("bool_"):
            data_var_name = "data_" + base_name[5:]
            if data_var_name in self.solver.variable_registry:
                rum[data_var_name] = self.solver.variable_registry[data_var_name]
                Logger.debug(f"[Executor] Variable cruciale '{data_var_name}' propagée vers le RUM.")

    # =====================================================
    # FORMATAGE REGISTRE
    # =====================================================

    def _format_registry_view(self, registry_view: dict) -> str:
        if not registry_view:
            return _("(registre vide)")
        lines = []
        for key, meta in registry_view.items():
            lines.append(f"- {key} : {meta.get('description', '')} (source: {meta.get('source', '')}, type: {meta.get('type', '')})")
            lines.append(f"  hint: {meta.get('value_hint', '')}")
        return "\n".join(lines)

    # =====================================================
    # INTERPOLATION (MÉTHODE CENTRALE AMÉLIORÉE)
    # =====================================================

    def _interpolate_text(self, text: str, for_json: bool = False) -> str:
        """
        Remplace les références $@_nom dans le texte par la valeur de la variable.
        Si for_json=True, les valeurs complexes (dict, list) sont sérialisées en JSON.
        Si for_json=False, on utilise str() pour un affichage lisible.
        """
        if not text:
            return text
        def replace_var(match):
            var_name = match.group(1)
            if var_name in self.solver.variable_registry:
                value = self.solver.variable_registry[var_name]["value"]
                if for_json:
                    # Contexte JSON : on sérialise en JSON si possible
                    try:
                        return json.dumps(value, ensure_ascii=False)
                    except TypeError:
                        Logger.warning(f"[Executor] Impossible de sérialiser '{var_name}' en JSON. Utilisation de str().")
                        return str(value)
                else:
                    # Contexte textuel : affichage lisible
                    if isinstance(value, (dict, list)):
                        # Pour les structures, on peut afficher un JSON compact mais lisible
                        return json.dumps(value, ensure_ascii=False)
                    else:
                        return str(value)
            return f"__UNKNOWN_VAR_{var_name}__"
        return re.sub(r'\$@_([a-zA-Z0-9_]+)', replace_var, text)

    def _interpolate_dict(self, obj, for_json: bool = True):
        """
        Parcourt récursivement un objet et interpole les chaînes.
        Par défaut, on utilise le mode JSON (for_json=True) car appelé pour tool_args_json.
        """
        if isinstance(obj, dict):
            return {k: self._interpolate_dict(v, for_json) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._interpolate_dict(item, for_json) for item in obj]
        elif isinstance(obj, str):
            return self._interpolate_text(obj, for_json)
        else:
            return obj

    # =====================================================
    # EXÉCUTION DES ÉTAPES
    # =====================================================

    async def _execute_step_action(self, step: PlanStep, runtime_context: str) -> Tuple[bool, str, Optional[str]]:
        if step.type == StepType.DIRECT_ANSWER:
            final_text = step.response_text or _("Réponse formulée.")
            if final_text:
                final_text = self._interpolate_text(final_text, for_json=False)
            return True, final_text, None
        elif step.type == StepType.TOOL_CALL:
            return await self._handle_tool_call(step)
        else:
            Logger.error(f"[Executor] 🛑 Type de step non pris en charge dans _execute_step_action : '{step.type}'")
            return False, "", _("Erreur de spécification : Le type d'étape '{}' est introuvable.").format(step.type)

    async def _handle_abstract_task(self, step: PlanStep, runtime_context: str) -> Tuple[bool, str, Optional[str], Optional[SolverResult]]:
        from .solver import Solver, MAX_DEPTH

        if self.solver.depth >= MAX_DEPTH:
            # Au lieu d'avorter systématiquement : on construit la chaîne
            # d'ancêtres (racine -> Solver courant, uniquement des Solvers —
            # on s'arrête dès qu'on atteint l'Orchestrateur, qui n'a ni
            # .depth ni .goal de la même forme) et on demande au juge de
            # l'Orchestrateur si cette profondeur reflète une décomposition
            # légitime d'un problème complexe, ou un motif récursif dégénéré.
            #
            # Note de conception : on ne "réinitialise" PAS self.solver.depth
            # (qui doit rester le reflet fidèle de la vraie profondeur de
            # l'arbre, notamment pour l'observabilité). Une extension
            # accordée permet seulement de franchir CE contrôle précis ; le
            # Solver enfant créé aura quand même depth+1, donc si LUI aussi
            # tente un abstract_task au-delà de MAX_DEPTH, une NOUVELLE
            # extension sera redemandée — et Orchestrator.request_depth_
            # extension plafonne le nombre total d'extensions accordées par
            # mission, indépendamment de l'avis du juge à chaque fois. Sans
            # ce plafond absolu, un juge trompé de façon répétée pourrait
            # neutraliser complètement le garde-fou de profondeur.
            ancestor_chain = self.solver._build_ancestor_chain()

            try:
                extended = await self.solver.request_depth_extension(ancestor_chain, step.id)
            except Exception as e:
                Logger.error(f"[Executor] Échec de la demande d'extension de profondeur : {e}")
                extended = False

            if not extended:
                return False, "", _("Profondeur maximale de réflexion atteinte, sous-tâche avortée (extension non accordée)."), None

            Logger.info(
                f"[Executor] 🔓 Profondeur étendue pour [{step.id}] : le juge n'a pas détecté de "
                f"récursion dégénérée dans la chaîne de sous-tâches actuelle."
            )

        Logger.info(f"[Executor] 🌀 Déploiement d'un sous-agent pour l'identifiant [{step.id}]")

        # BUG CORRIGÉ (root cause d'une boucle infinie observée en test réel) :
        # step.step_context et step.description peuvent contenir des références
        # $@_nom (ex: "$@_data_target_apps") destinées à transmettre au Solver
        # enfant des données déjà résolues par le PARENT. Elles n'étaient
        # JAMAIS interpolées avant d'être transmises — le Planner enfant
        # recevait donc littéralement la chaîne "$@_data_target_apps" au lieu
        # de la vraie liste d'applications. Sans donnée concrète sur laquelle
        # agir, il ne pouvait que re-déléguer à un nouvel abstract_task avec
        # un objectif tout aussi vague — qui reproduisait le même placeholder
        # non résolu, indéfiniment. L'interpolation utilise le registre du
        # solver PARENT (self, ici) — c'est lui qui détient la valeur, pas
        # l'enfant, qui n'a pas encore de registre à ce stade.
        interpolated_step_context = self._interpolate_text(step.step_context, for_json=False) if step.step_context else ""
        interpolated_goal = self._interpolate_text(step.description, for_json=False)

        if interpolated_step_context != (step.step_context or "") or interpolated_goal != step.description:
            Logger.debug(
                f"[Executor] Variables interpolées pour la sous-tâche [{step.id}] "
                f"avant délégation au Solver enfant (évite la perte de données entre niveaux)."
            )

        dynamic_context = (
            _("--- DIRECTIVE LOCALE SPÉCIFIQUE À CETTE TÂCHE ---\n") +
            _("{}").format(interpolated_step_context or _('Pas de donnees supplementaires'))
        )

        child_solver = Solver(
            solver_id=step.id,
            goal=interpolated_goal,
            parent=self.solver,
            provider_manager=self.solver.provider_manager,
            runtime_state=self.solver.runtime_state,
            llm=self.solver.llm,
            depth=self.solver.depth + 1,
            context=dynamic_context,
            parent_step_id=step.id
        )

        child_result = await child_solver.run()

        if step.output_variable_name:
            base_name = step.output_variable_name
            status_value = "true" if child_result.status == ExecutionStatus.SUCCESS else "false"

            # --- 1. Création du booléen (inchangé) ---
            self.solver.variable_registry[base_name] = {
                "value": status_value,
                "description": step.output_variable_desc or _("Statut de l'étape abstraite {}").format(step.id),
                "source": child_solver.id,
                "timestamp": datetime.now().isoformat()
            }

            # --- 2. Création de la variable data_* (améliorée) ---
            if base_name.startswith("bool_"):
                data_var_name = "data_" + base_name[5:]
            else:
                data_var_name = base_name + "_data"

            if child_result.status == ExecutionStatus.SUCCESS:
                data_value = child_result.response or _("Succès de l'étape abstraite {}").format(step.id)
                data_description = (
                    step.output_variable_desc or _("Réponse de l'étape abstraite {}").format(step.id)
                ) + _(" (statut: {})").format(status_value)
            else:
                data_value = child_result.error_reason or _("Échec de l'étape abstraite {}").format(step.id)
                data_description = (
                    step.output_variable_desc or _("Erreur de l'étape abstraite {}").format(step.id)
                ) + _(" (statut: {})").format(status_value)

            self.solver.variable_registry[data_var_name] = {
                "value": data_value,
                "description": data_description,
                "source": child_solver.id,
                "timestamp": datetime.now().isoformat()
            }

            if step.is_crucial:
                self._propagate_crucial_variable(base_name)

        if child_result.status == ExecutionStatus.SUCCESS:
            final_response = child_result.response or _("Objectif de la sous-tâche [{}] atteint.").format(step.id)
            enriched_response = f"[TOOLS OK] {final_response}"
            return True, enriched_response, child_result.final_context, child_result
        else:
            error_msg = child_result.error_reason or "Échec de la sous-tâche"
            enriched_error = f"[TOOLS FAILED] {error_msg}"
            return False, "", enriched_error, child_result

    async def _handle_tool_call(self, step: PlanStep) -> Tuple[bool, str, Optional[str]]:
        try:
            tool_args = self._safe_json_loads(step.tool_args_json)
        except json.JSONDecodeError as e:
            error_detail = f"{e.msg} at line {e.lineno} column {e.colno} (pos {e.pos})"
            return False, "", _("Échec de désérialisation JSON initial : {}").format(error_detail)

        # --- SI tool_manager, on garde les arguments bruts (avec $@_) ---
        if step.tool_name == "tool_manager":
            tool_args_raw = tool_args.copy()
            Logger.debug(f"[Executor] tool_manager appelé avec arguments bruts : {tool_args_raw}")
            # PASSER LE REGISTRE DU SOLVER AU RUNTIME_STATE (via le solver)
            self.solver.runtime_state._solver_registry_for_tools = self.solver.variable_registry
        else:
            tool_args_raw = self._interpolate_dict(tool_args, for_json=True)

        hardware_result_str = await self.solver.execute_tool(step.tool_name, tool_args_raw)

        # --- NETTOYER L'ATTRIBUT TEMPORAIRE ---
        if step.tool_name == "tool_manager":
            self.solver.runtime_state._solver_registry_for_tools = None

        if hardware_result_str:
            hardware_result_str = hardware_result_str.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

        try:
            parsed_result = json.loads(hardware_result_str)
            is_success_flag = str(parsed_result.get("result", "")).strip().lower()
            actual_data = parsed_result.get("data", None)

            if step.output_variable_name:
                base_name = step.output_variable_name

                # --- 1. Création du booléen (inchangé) ---
                self.solver.variable_registry[base_name] = {
                    "value": is_success_flag,
                    "description": step.output_variable_desc or _("Statut de l'opération {}").format(step.tool_name),
                    "source": self.solver.id,
                    "timestamp": datetime.now().isoformat()
                }

                # --- 2. Création de la variable data_* (améliorée) ---
                if base_name.startswith("bool_"):
                    data_var_name = "data_" + base_name[5:]
                else:
                    data_var_name = base_name + "_data"

                if actual_data is not None:
                    data_description = (
                        step.output_variable_desc or _("Données retournées par {}").format(step.tool_name)
                    ) + _(" (statut: {})").format(is_success_flag)
                else:
                    data_description = _("Aucune donnée retournée par {} (statut: {})").format(
                        step.tool_name, is_success_flag
                    )

                self.solver.variable_registry[data_var_name] = {
                    "value": actual_data,  # peut être None
                    "description": data_description,
                    "source": self.solver.id,
                    "timestamp": datetime.now().isoformat()
                }

                if step.is_crucial:
                    self._propagate_crucial_variable(base_name)

            return True, is_success_flag, None

        except json.JSONDecodeError:
            Logger.warning(f"[Executor] Échec de parsing JSON pour l'outil {step.tool_name}. Contenu reçu : {hardware_result_str[:200]}")
            return False, "", "Le retour de l'outil C++ ne respecte pas le format JSON strict."

    # =====================================================
    # CONVERGENCE ET VALIDATION
    # =====================================================

    async def _check_convergence(self, step: PlanStep, actual_result: str) -> ConvergenceDecision:
        if not step.expected_result:
            Logger.info(f"[Executor] Aucun critère défini pour [{step.id}]. Convergence implicite acceptée.")
            return ConvergenceDecision(is_convergent=True, reason=_("Aucun critère d'output spécifié."))

        if step.type == StepType.DIRECT_ANSWER:
            return ConvergenceDecision(is_convergent=True, reason="Les réponses directes sont toujours acceptées.")

        if step.type == StepType.TOOL_CALL:
            Logger.info(f"[Executor] [Analyse Rigide] Évaluation déterministe pour l'outil '{step.tool_name}'...")
            is_valid, rigid_reason = self._verify_rigid_outcome(step.expected_result, actual_result)
            return ConvergenceDecision(is_convergent=is_valid, reason=rigid_reason)

        Logger.info(f"[Executor] [Analyse Sémantique] Invocation du LLM pour l'étape abstraite [{step.id}]...")
        return await self._evaluate_semantic_convergence(step, actual_result)

    def _verify_rigid_outcome(self, expected: str, actual: str) -> Tuple[bool, str]:
        expected_clean = expected.strip().lower()
        actual_clean = actual.strip().lower()

        if expected_clean == "any":
            return True, _("Convergence acceptée : Le plan accepte toute valeur de retour pour traitement conditionnel ultérieur.")

        if expected_clean not in ["true", "false"]:
            return False, _("Défaut de planification : expected_result doit valoir 'true', 'false' ou 'any', mais vaut '{}'.").format(expected)

        if expected_clean == actual_clean:
            return True, _("Validation stricte réussie.")

        return False, _("Rejet matériel : L'outil a renvoyé '{}', mais le plan exigeait expressément '{}'.").format(actual_clean, expected_clean)

    async def _evaluate_semantic_convergence(self, step: PlanStep, actual_result: str) -> ConvergenceDecision:
        mission_id = None
        if hasattr(self.solver, 'runtime_state') and self.solver.runtime_state:
            mission_id = self.solver.runtime_state.execution_context.get("mission_id")

        loader = get_prompt_loader()
        prompt = loader.load(
            "convergence.md",
            lang=self.solver.runtime_state.language,
            step_description=step.description,
            expected_result=step.expected_result,
            actual_result=actual_result
        )
        try:
            return await self.solver.llm.generate_structured(
                prompt=prompt,
                schema=ConvergenceDecision,
                tag="ConvergenceDecision",
                mission_id=mission_id
            )
        except Exception as e:
            Logger.error(f"[Executor] 🔥 Panne de l'infrastructure de validation sémantique à l'étape [{step.id}] : {str(e)}")
            raise e

    # =====================================================
    # UTILITAIRES (JSON, conditions, etc.)
    # =====================================================

    def _safe_json_loads(self, json_str: str) -> dict:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            if "Invalid \\escape" not in str(e):
                raise

            result = []
            i = 0
            while i < len(json_str):
                if json_str[i] == '\\' and i + 1 < len(json_str):
                    nxt = json_str[i + 1]
                    if nxt in ('"', '\\', '/', 'b', 'f', 'n', 'r', 't'):
                        result.append(json_str[i])
                        result.append(nxt)
                        i += 2
                        continue
                    elif nxt == 'u':
                        result.append(json_str[i])
                        result.append(nxt)
                        i += 2
                        continue
                    else:
                        result.append('\\\\')
                        result.append(nxt)
                        i += 2
                        continue
                result.append(json_str[i])
                i += 1

            fixed_str = ''.join(result)
            return json.loads(fixed_str)

    def _normalize_condition(self, expr: str) -> str:
        import re
        expr = expr.replace('!=', '___NEQ___')
        expr = expr.replace('&&', ' and ')
        expr = expr.replace('||', ' or ')
        expr = expr.replace('!', ' not ')
        expr = re.sub(r'\bAND\b', ' and ', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bOR\b', ' or ', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bNOT\b', ' not ', expr, flags=re.IGNORECASE)
        expr = expr.replace('___NEQ___', '!=')
        return expr

    def _build_failure_trace(self, executed_steps_trace: List[str], current_step: PlanStep, failure_type: str, details: str) -> str:
        trace_lines = []
        trace_lines.append("=== RAPPORT D’ÉCHEC POUR RE-PLANIFICATION ===")

        if executed_steps_trace:
            trace_lines.append("✅ ÉTAPES RÉUSSIES AVANT L’ÉCHEC :")
            trace_lines.extend(executed_steps_trace)
        else:
            trace_lines.append("❌ Aucune étape n’a été validée avant l’échec.")

        trace_lines.append("")
        trace_lines.append("💥 ÉTAPE DE RUPTURE :")
        trace_lines.append(f"- ID : {current_step.id}")
        trace_lines.append(f"- Action : {current_step.description}")
        if current_step.type == StepType.TOOL_CALL:
            trace_lines.append(f"- Outil : {current_step.tool_name}")
            trace_lines.append(f"- Arguments : {current_step.tool_args_json}")
        trace_lines.append(f"- Résultat attendu : {current_step.expected_result}")
        trace_lines.append(f"- Nature de l’échec : {failure_type}")
        trace_lines.append(f"- Détail technique : {details}")

        trace_lines.append("")
        trace_lines.append("============================================")
        return "\n".join(trace_lines)

    def _evaluate_condition(self, condition_raw: str) -> bool:
        import ast
        import operator
        import re

        interpolated = self._interpolate_text(condition_raw, for_json=False)

        if "__UNKNOWN_VAR_" in interpolated:
            Logger.debug(f"[Executor] Condition contient une variable inconnue, évaluée à False : {condition_raw}")
            return False

        interpolated = interpolated.replace(_("[Action ignorée]"), "False")
        interpolated = re.sub(r'\btrue\b', 'True', interpolated, flags=re.IGNORECASE)
        interpolated = re.sub(r'\bfalse\b', 'False', interpolated, flags=re.IGNORECASE)
        interpolated = self._normalize_condition(interpolated)

        Logger.debug(f"[Executor] Évaluation de la condition normalisée : '{interpolated}'")

        operators = {
            ast.Eq: operator.eq, ast.NotEq: operator.ne,
            ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge,
            ast.And: lambda a, b: a and b,
            ast.Or: lambda a, b: a or b,
        }
        try:
            tree = ast.parse(interpolated, mode='eval')

            def eval_node(node):
                if isinstance(node, ast.Constant):
                    return node.value
                elif isinstance(node, ast.Name):
                    return node.id
                elif isinstance(node, ast.Compare):
                    left = eval_node(node.left)
                    op_type = type(node.ops[0])
                    right = eval_node(node.comparators[0])
                    if op_type in operators:
                        if isinstance(left, bool) and isinstance(right, str) and right.lower() in ["true", "false"]:
                            right = right.lower() == "true"
                        elif isinstance(right, bool) and isinstance(left, str) and left.lower() in ["true", "false"]:
                            left = left.lower() == "true"
                        return operators[op_type](left, right)
                    raise ValueError(_("Opérateur de comparaison non supporté : {}").format(op_type))
                elif isinstance(node, ast.BoolOp):
                    values = [eval_node(v) for v in node.values]
                    op_type = type(node.op)
                    if op_type == ast.And:
                        return all(values)
                    elif op_type == ast.Or:
                        return any(values)
                raise ValueError(_("Expression non autorisée : {}").format(type(node)))

            result = eval_node(tree.body)
            return bool(result)

        except Exception as e:
            Logger.error(f"[Executor] ⚠️ Erreur syntaxique ou typage dans la condition '{condition_raw}' (Normalisé: '{interpolated}'). Motif: {e}")
            return False
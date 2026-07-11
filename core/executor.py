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
    """
    Composant d'ingénierie logiciel chargé de l'exécution séquentielle et récursive d'un Plan.
    Garantit le confinement des espaces d'exécution par étape et orchestre la validation 
    des résultats via une double couche de contrôle : rigide (déterministe) et sémantique (LLM).
    """

    def __init__(self, solver_node):
        """
        Initialise l'exécuteur en le liant à son nœud superviseur parent.
        
        :param solver_node: Instance du Solver supervisant cette session d'exécution.
        """
        self.solver = solver_node 

    async def execute_plan(self, plan: Plan, current_context: str, current_attempt: PlanAttempt) -> SolverResult:
        Logger.info(f"[Executor] 🚀 Initialisation de l'exécution du plan : '{plan.goal}'")

        user_responses: List[str] = []
        executed_steps_trace: List[str] = []  # conservé pour le rapport d'échec existant
        accumulated_context = current_context

        try:
            for step in plan.steps:
                if self.solver.runtime_state.cancel_requested:
                    Logger.warning(f"[Executor] 🛑 Interruption demandée à l'étape [{step.id}].")
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=accumulated_context,
                        error_reason=_("Exécution interrompue"),
                        failure_class=FailureClass.USER_CANCELLED  # <--- NOUVEAU
                    )

                # --- CRÉATION DU NŒUD D'EXÉCUTION ---
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

                # --- ÉTAPE CONDITIONNELLE ---
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
                            "reason": _("Condition ({}) non remplie").format(step.execute_if)
                        })

                        step.result_context = _("Étape ignorée par branchement conditionnel : {}").format(step.execute_if)
                        executed_steps_trace.append(_("- [{}] Sautée (Condition non remplie)").format(step.id))
                        continue

                Logger.info(f"[Executor] ⚙️ Traitement de l'étape [{step.id}] -> {step.description}")
                await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                    "step_id": step.id,
                    "status": ExecutionStatus.RUNNING,
                    "description": step.description
                })

                # --- EXÉCUTION DE L'ACTION (selon le type) ---
                if step.type == StepType.ABSTRACT_TASK:
                    success, execution_output, supplemental_data, child_result = await self._handle_abstract_task(step, accumulated_context)
                    # Attacher l'arbre du child si disponible
                    if child_result and child_result.execution_tree:
                        node.child_execution_tree = child_result.execution_tree
                else:
                    # DIRECT_ANSWER ou TOOL_CALL
                    success, execution_output, supplemental_data = await self._execute_step_action(step, accumulated_context)

                # --- A.3 (NOUVEAU) : capturer le booléen BRUT de l'outil dès maintenant, avant
                # tout jugement de convergence — c'est la seule façon de distinguer plus tard
                # "cette étape a réussi" de "cette étape a été TOLÉRÉE malgré un échec réel"
                # (expected_result='any'). Uniquement pertinent pour un tool_call qui a pu
                # être désérialisé (success=True au sens de _handle_tool_call, indépendamment
                # de la convergence qui sera jugée juste après).
                if step.type == StepType.TOOL_CALL and success and execution_output is not None:
                    node.raw_tool_success = str(execution_output).strip().lower() == "true"

                # --- CHECKPOINT ANNULATION ---
                if self.solver.runtime_state.cancel_requested:
                    Logger.warning(f"[Executor] 🛑 Interruption après action de l'étape [{step.id}].")
                    node.status = ExecutionStatus.FAILED
                    node.error_reason = _("Arrêté par l'utilisateur.")
                    node.ended_at = time.time()
                    return SolverResult(status=ExecutionStatus.FAILED, final_context=accumulated_context,
                                        error_reason=_("Arrêté par l'utilisateur."))

                # --- GESTION DE L'ÉCHEC FONCTIONNEL ---
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
                        "reason": step.result_context
                    })

                    failure_trace = self._build_failure_trace(
                        executed_steps_trace, step, _("Échec d'exécution de l'action"), step.result_context
                    )
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=accumulated_context + f"\n{failure_trace}",
                        error_reason=_("Échec à l'étape {} : {}").format(step.id, step.result_context),
                        failure_class=FailureClass.EXECUTION_FAILURE,  # <--- NOUVEAU
                        target_entity="Executor"  # <--- NOUVEAU : blâme posé à la source, pas deviné plus tard
                    )

                # --- CONTRÔLE DE CONVERGENCE ---
                convergence = await self._check_convergence(step, execution_output)

                if self.solver.runtime_state.cancel_requested:
                    Logger.warning(f"[Executor] 🛑 Interruption après convergence de l'étape [{step.id}].")
                    node.status = ExecutionStatus.FAILED
                    node.error_reason = _("Generation stoppee.")
                    node.ended_at = time.time()
                    return SolverResult(status=ExecutionStatus.FAILED, final_context=accumulated_context,
                                        error_reason=_("Generation stoppee."),
                                        failure_class=FailureClass.USER_CANCELLED )

                if convergence.is_convergent:
                    Logger.info(f"[Executor] ✅ Étape [{step.id}] validée.")
                    step.status = ExecutionStatus.SUCCESS
                    step.result_context = execution_output
                    node.status = ExecutionStatus.SUCCESS
                    node.actual_result = execution_output
                    node.ended_at = time.time()

                    await self.solver.propagate_event(Events.STEP_STATUS_CHANGED, {
                        "step_id": step.id,
                        "status": ExecutionStatus.SUCCESS,
                        "result": execution_output
                    })

                    executed_steps_trace.append(_("- [{}] Validée : Output={}").format(step.id, execution_output))
                    accumulated_context += f"\n[Succès {step.id}] : {execution_output}"

                    if step.type in [StepType.DIRECT_ANSWER, StepType.ABSTRACT_TASK]:
                        if execution_output:
                            user_responses.append(execution_output)
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
                        "reason": convergence.reason
                    })

                    failure_trace = self._build_failure_trace(
                        executed_steps_trace, step, _("Divergence de résultat (Non-convergence)"), convergence.reason
                    )
                    return SolverResult(
                        status=ExecutionStatus.FAILED,
                        final_context=accumulated_context + f"\n{failure_trace}",
                        error_reason=_("Divergence détectée à l'étape {} : {}").format(step.id, convergence.reason),
                        failure_class=FailureClass.CONVERGENCE_FAILURE,  # <--- NOUVEAU
                        target_entity="Executor"  # <--- NOUVEAU
                    )

            # --- FIN DU PLAN : TOUT A RÉUSSI ---
            final_user_text = "\n\n".join([r for r in user_responses if r])
            final_user_text = self._interpolate_text(final_user_text)

            Logger.info("[Executor] 🎉 Fin de traitement : toutes les étapes ont convergé.")
            return SolverResult(
                status=ExecutionStatus.SUCCESS,
                final_context=accumulated_context,
                response=final_user_text or _("Mission [{}] accomplie.").format(self.solver.id),
                resolved_data=self.solver.variable_registry,
                failure_class=None  # <--- NOUVEAU (explicite)
            )

        except Exception as e:
            Logger.error(f"[Executor] 🔥 Exception critique : {str(e)}")
            raise e
            
    # =====================================================
    # DISPATCHER D'ACTIONS
    # =====================================================
    async def _execute_step_action(self, step: PlanStep, runtime_context: str) -> Tuple[bool, str, Optional[str]]:
        if step.type == StepType.DIRECT_ANSWER:
            final_text = step.response_text or _("Réponse formulée.")
            if final_text:
                final_text = self._interpolate_text(final_text)
            return True, final_text, None
        elif step.type == StepType.TOOL_CALL:
            return await self._handle_tool_call(step)
        else:
            # Ne devrait pas arriver car ABSTRACT_TASK est traité ailleurs
            Logger.error(f"[Executor] 🛑 Type de step non pris en charge dans _execute_step_action : '{step.type}'")
            return False, "", _("Erreur de spécification : Le type d'étape '{}' est introuvable.").format(step.type)
    # =====================================================
    # INTERPOLATEUR TEXTE LIBRE (Ajout à la suite de ton code)
    # =====================================================
    def _interpolate_text(self, text: str) -> str:
        """Remplace proprement les variables $@_ par leur valeur ou un texte de repli."""
        if not text:
            return text
        matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', text)
        interpolated = text
        for var_name in matches:
            if var_name in self.solver.variable_registry:
                real_value = self.solver.variable_registry[var_name]["value"]
                interpolated = interpolated.replace(f"$@_{var_name}", str(real_value))
            else:
                # FIX : Si l'étape a été sautée, la variable n'existe pas.
                interpolated = interpolated.replace(f"$@_{var_name}", _("[Action ignorée]"))
        return interpolated
    
    async def _handle_abstract_task(self, step: PlanStep, runtime_context: str) -> Tuple[bool, str, Optional[str], Optional[SolverResult]]:
        """
        Exécute une sous-tâche abstraite en créant un Solver enfant.
        Retourne (succès, output, context_supplémentaire, child_result).
        """
        from .solver import Solver, MAX_DEPTH

        if self.solver.depth >= MAX_DEPTH:
            return False, "", _("Profondeur maximale de réflexion atteinte, sous-tâche avortée."), None

        Logger.info(f"[Executor] 🌀 Déploiement d'un sous-agent pour l'identifiant [{step.id}]")

        dynamic_context = (
            _("--- DIRECTIVE LOCALE SPÉCIFIQUE À CETTE TÂCHE ---\n") +
            _("{}").format(step.step_context or _('Pas de donnees supplementaires'))
        )

        child_solver = Solver(
            solver_id=step.id,
            goal=step.description,
            parent=self.solver,
            provider_manager=self.solver.provider_manager,
            runtime_state=self.solver.runtime_state,
            llm=self.solver.llm,
            depth=self.solver.depth + 1,
            context=dynamic_context,
            parent_step_id=step.id
        )

        child_result = await child_solver.run()

        # --- GESTION DE output_variable_name ---
        if step.output_variable_name:
            base_name = step.output_variable_name
            status_value = "true" if child_result.status == ExecutionStatus.SUCCESS else "false"
            self.solver.variable_registry[base_name] = {
                "value": status_value,
                "description": step.output_variable_desc or _("Statut de l'étape abstraite {}").format(step.id),
                "source": child_solver.id,
                "timestamp": datetime.now().isoformat()
            }
            data_var_name = base_name + "_data"
            data_value = child_result.response if child_result.status == ExecutionStatus.SUCCESS else (child_result.error_reason or "Échec")
            self.solver.variable_registry[data_var_name] = {
                "value": data_value,
                "description": step.output_variable_desc or _("Réponse de l'étape abstraite {}").format(step.id),
                "source": child_solver.id,
                "timestamp": datetime.now().isoformat()
            }

        if child_result.status == ExecutionStatus.SUCCESS:
            final_response = child_result.response or _("Objectif de la sous-tâche [{}] atteint.").format(step.id)
            return True, final_response, child_result.final_context, child_result
        else:
            return False, "", child_result.error_reason, child_result    
            
    # =====================================================
    # ROUTINES LOGIQUES INTERNES
    # =====================================================
    async def _handle_tool_call(self, step: PlanStep) -> Tuple[bool, str, Optional[str]]:
        # 1. Parser le JSON en dictionnaire
        try:
            tool_args = json.loads(step.tool_args_json)
        except json.JSONDecodeError as e:
            error_detail = f"{e.msg} at line {e.lineno} column {e.colno} (pos {e.pos})"
            return False, "", _("Échec de désérialisation JSON initial : {}").format(error_detail)

        # 2. Interpoler les variables
        tool_args = self._interpolate_dict(tool_args, self.solver.variable_registry)

        # 3. Appel matériel
        hardware_result_str = await self.solver.execute_tool(step.tool_name, tool_args)
        
        # 3. PARSING DU RETOUR NORMALISÉ (C++ DOIT renvoyer un JSON)
        try:
            parsed_result = json.loads(hardware_result_str)
            is_success_flag = str(parsed_result.get("result", "")).strip().lower()
            actual_data = parsed_result.get("data", None)
            
            # 4. SAUVEGARDE DANS LE REGISTRE AVEC REPLI SÉCURISÉ
            if step.output_variable_name:
                base_name = step.output_variable_name

                # 1. Stocker le booléen (toujours)
                self.solver.variable_registry[base_name] = {
                    "value": is_success_flag,  # "true" ou "false"
                    "description": step.output_variable_desc or _("Statut de l'opération {}").format(step.tool_name),
                    "source": self.solver.id,
                    "timestamp": datetime.now().isoformat()
                }

                # 2. Stocker les données (si présentes)
                if actual_data is not None:
                    data_var_name = base_name + "_data"
                    self.solver.variable_registry[data_var_name] = {
                        "value": actual_data,  # peut être une chaîne, une liste, un dict, etc.
                        "description": step.output_variable_desc or _("Données retournées par {}").format(step.tool_name),
                        "source": self.solver.id,
                        "timestamp": datetime.now().isoformat()
                    }
                    
            # On retourne l'état binaire strict pour la vérification de convergence
            return True, is_success_flag, None
            
        except json.JSONDecodeError:
             return False, "", "Le retour de l'outil C++ ne respecte pas le format JSON strict."
            
    # =====================================================
    # COUCHE DE VÉRIFICATION DE CONVERGENCE FILTRÉE
    # =====================================================
    async def _check_convergence(self, step: PlanStep, actual_result: str) -> ConvergenceDecision:
        """
        Évalue le résultat obtenu face aux critères attendus.
        Pour un Tool Call, applique une validation technique rigide immédiate sans LLM.
        Pour une tâche abstraite, applique une analyse sémantique via LLM.
        
        :param step: L'étape contenant le critère d'évaluation défini par le Planner.
        :param actual_result: L'output réel produit par l'action.
        :return: Une instance de ConvergenceDecision.
        """
        if not step.expected_result:
            Logger.info(f"[Executor] Aucun critère défini pour [{step.id}]. Convergence implicite acceptée.")
            return ConvergenceDecision(is_convergent=True, reason= _("Aucun critère d'output spécifié."))

        if step.type == StepType.DIRECT_ANSWER:
            return ConvergenceDecision(is_convergent=True, reason="Les réponses directes sont toujours acceptées.")
        # --- TEMPS 1 : CONVERGENCE TECHNIQUE ET RIGIDE (Pour les appels d'outils) ---
        if step.type == StepType.TOOL_CALL:
            Logger.info(f"[Executor] [Analyse Rigide] Évaluation déterministe pour l'outil '{step.tool_name}'...")
            is_valid, rigid_reason = self._verify_rigid_outcome(step.expected_result, actual_result)
            
            # Pour un outil, l'évaluation rigide fait foi. Si elle échoue ou réussit, on tranche directement.
            return ConvergenceDecision(is_convergent=is_valid, reason=rigid_reason)

        # --- TEMPS 2 : CONVERGENCE SÉMANTIQUE (Pour les tâches abstraites ou réponses directes) ---
        Logger.info(f"[Executor] [Analyse Sémantique] Invocation du LLM pour l'étape abstraite [{step.id}]...")
        return await self._evaluate_semantic_convergence(step, actual_result)

    # =====================================================
    # VALIDATIONS PRIVÉES ET OUTILS DE DIAGNOSTIC
    # =====================================================
    def _verify_rigid_outcome(self, expected: str, actual: str) -> Tuple[bool, str]:
        """Évaluation déterministe de la réponse matérielle."""
        expected_clean = expected.strip().lower()
        actual_clean = actual.strip().lower()

        # Si le plan accepte n'importe quel retour d'état pour le stocker et bifurquer plus tard
        if expected_clean == "any":
            return True, _("Convergence acceptée : Le plan accepte toute valeur de retour pour traitement conditionnel ultérieur.")

        if expected_clean not in ["true", "false"]:
            return False, _("Défaut de planification : expected_result doit valoir 'true', 'false' ou 'any', mais vaut '{}'.").format(expected)

        if expected_clean == actual_clean:
            return True, _("Validation stricte réussie.")
            
        return False, _("Rejet matériel : L'outil a renvoyé '{}', mais le plan exigeait expressément '{}'.").format(actual_clean, expected_clean)
    

    # async def _evaluate_semantic_convergence(self, step: PlanStep, actual_result: str) -> ConvergenceDecision:
    #     """
    #     Soumet les critères comportementaux d'une macro-tâche à une évaluation sémantique par modèle de langage.
    #     """
    #     prompt = f"""
    #     Tu es le module expert de vérification sémantique de l'architecture de traitement.
    #     Ton unique rôle est de valider si le résultat textuel obtenu à la suite d'une macro-tâche répond aux exigences logiques fixées par le plan.

    #     INFORMATIONS DE RÉFÉRENCE :
    #     - Tâche exécutée : {step.description}
    #     - Résultat attendu visé (Expected Output) : {step.expected_result}
        
    #     RÉSULTAT RÉEL OBTENU :
    #     {actual_result}
        
    #     DIRECTIVES STRICTES DE VERDICT :
    #     1. Compare de manière critique l'output réel face aux exigences du résultat attendu.
    #     2. Si la tâche a produit des effets conformes sémantiquement aux attentes, positionne 'is_convergent' à true.
    #     3. Si le résultat indique une omission ou ne remplit pas l'attendu, positionne 'is_convergent' à false et consigne une explication technique détaillée dans le champ 'reason'.
    #     """
    #     try:
    #         # Toute exception API réseau remonte naturellement vers le superviseur
    #         return await self.solver.llm.generate_structured(prompt=prompt, schema=ConvergenceDecision)
    #     except Exception as e:
    #         Logger.error(f"[Executor] 🔥 Panne de l'infrastructure de validation sémantique à l'étape [{step.id}] : {str(e)}")
    #         raise e


    async def _evaluate_semantic_convergence(self, step: PlanStep, actual_result: str) -> ConvergenceDecision:
        """
        Soumet les critères comportementaux d'une macro-tâche à une évaluation sémantique par modèle de langage.
        """
        loader = get_prompt_loader()
        prompt = loader.load(
            "convergence.md",
            lang=self.solver.runtime_state.language,
            step_description=step.description,
            expected_result=step.expected_result,
            actual_result=actual_result
        )
        try:
            return await self.solver.llm.generate_structured(prompt=prompt, schema=ConvergenceDecision)
        except Exception as e:
            Logger.error(f"[Executor] 🔥 Panne de l'infrastructure de validation sémantique à l'étape [{step.id}] : {str(e)}")
            raise e
        
    def _normalize_condition(self, expr: str) -> str:
        """
        Remplace les opérateurs de style C/JS (&&, ||, !) par leurs équivalents Python (and, or, not).
        Protège les '!=' pour ne pas les transformer en 'not ='.
        """
        # 1. Protéger '!=' (ne doit pas être transformé)
        expr = expr.replace('!=', '___NEQ___')
        # 2. Remplacer '&&' par 'and' (avec espaces pour éviter les collisions)
        expr = expr.replace('&&', ' and ')
        # 3. Remplacer '||' par 'or'
        expr = expr.replace('||', ' or ')
        # 4. Remplacer '!' par 'not' (sans toucher aux '!=')
        expr = expr.replace('!', ' not ')
        # 5. Restaurer '!='
        expr = expr.replace('___NEQ___', '!=')
        return expr
        
    def _build_failure_trace(self, executed_steps_trace: List[str], current_step: PlanStep, failure_type: str, details: str) -> str:
        """
        Formate et retourne un historique chirurgical structuré de la session de traitement en cours.
        Fournit au Planner le contexte nécessaire pour échafauder sa stratégie de correction au cycle suivant.
        """
        trace = _("\n=== [RAPPORT DE DIAGNOSTIC D'ÉCHEC POUR RE-PLANIFICATION] ===\n")
        if executed_steps_trace:
            trace += _("✅ ÉTAPES VALIDÉES AU COURS DE CETTE SESSION D'EXÉCUTION :\n")
            trace += "\n".join(executed_steps_trace) + "\n"
        else:
            trace += _("❌ Aucune étape intermédiaire n'a pu être validée avant cette panne.\n")
            
        trace += _("\n💥 ÉTAPE DE RUPTURE DE CONVERGENCE CONSTATÉE :\n")
        trace += _("- ID de l'Étape : {}\n").format(current_step.id)
        trace += _("- Description de l'Action : {}\n").format(current_step.description)
        trace += _("- Contexte Local Injecté : {}\n").format(current_step.step_context or _("Aucun (Espace Vide)"))
        trace += _("- Critère de Réussite Attendu (Expected Output) : {}\n").format(current_step.expected_result)
        trace += _("- Nature de la Défaillance : {}\n").format(failure_type)
        trace += _("- Justification du Contrôleur de flux : {}\n").format(details)
        trace += "==============================================================\n"
        return trace
    

    # def _interpolate_variables(self, raw_json: str) -> str:
    #     """Remplace les occurrences $@_nom_variable par les données du registre en sécurisant le format JSON."""
    #     matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', raw_json)
    #     interpolated = raw_json
        
    #     for var_name in matches:
    #         if var_name in self.solver.variable_registry:
    #             real_value = self.solver.variable_registry[var_name]["value"]
                
    #             # Sécurisation des caractères spéciaux (antislashs, guillemets internes, retours à la ligne)
    #             escaped_value = json.dumps(real_value)
                
    #             # Si le LLM a entouré le pointeur de guillemets (ex: "$@_ma_var"), 
    #             # json.dumps() rajoutant lui-même des guillemets, on extrait uniquement l'intérieur échappé.
    #             if f'"$@_{var_name}"' in interpolated:
    #                 # On retire les guillemets de début et de fin générés par dumps()
    #                 escaped_inside = escaped_value[1:-1]
    #                 interpolated = interpolated.replace(f"$@_{var_name}", escaped_inside)
    #             else:
    #                 # Cas où le LLM l'utilise comme valeur brute (ex: un entier ou un objet JSON complet)
    #                 interpolated = interpolated.replace(f"$@_{var_name}", escaped_value)
                    
    #     return interpolated
    

    def _evaluate_condition(self, condition_raw: str) -> bool:
        """
        Évalue de manière déterministe, typée et sécurisée une expression logique.
        Tolère la casse (true/True) et supporte and/or.
        """
        import ast
        import operator
        import re

        # 1. Interpolation avec gestion des variables manquantes
        # On utilise _interpolate_text qui remplace $@_var par sa valeur ou "[Action ignorée]"
        interpolated = self._interpolate_text(condition_raw).strip()
        # 2. Remplacer les variables manquantes par False pour l'AST
        interpolated = interpolated.replace(_("[Action ignorée]"), "False")

        # 3. Normalisation de la casse pour l'AST Python (true -> True, false -> False)
        interpolated = re.sub(r'\btrue\b', 'True', interpolated, flags=re.IGNORECASE)
        interpolated = re.sub(r'\bfalse\b', 'False', interpolated, flags=re.IGNORECASE)

        # ---> AJOUT : Normalisation des opérateurs C/JS
        interpolated = self._normalize_condition(interpolated)

        Logger.debug(f"[Executor] Évaluation de la condition normalisée : '{interpolated}'")

        # Table des opérateurs sécurisés (STRICTEMENT binaire et comparaison simple)
        operators = {
            ast.Eq: operator.eq, ast.NotEq: operator.ne,
            # ast.Lt, ast.Gt etc. restent acceptables si tu compares deux booléens/entiers générés par des outils
            ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge,
            ast.And: lambda a, b: a and b,
            ast.Or: lambda a, b: a or b,
            # RETRAIT VOLONTAIRE DE ast.In ET ast.NotIn
        }
        try:
            tree = ast.parse(interpolated, mode='eval')

            def eval_node(node):
                if isinstance(node, ast.Constant):
                    return node.value
                    
                # 🔥 LA CORRECTION EST ICI : Intercepter les mots sans guillemets (ex: Oui, Non)
                # L'AST voit 'Oui' comme un ast.Name. On retourne directement son nom ('Oui') sous forme de texte.
                elif isinstance(node, ast.Name):
                    return node.id
                    
                elif isinstance(node, ast.Compare):
                    left = eval_node(node.left)
                    op_type = type(node.ops[0])
                    right = eval_node(node.comparators[0])
                    if op_type in operators:
                        # Si on compare un booléen et une string, on force le type
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
            # Si le plan génère une condition illisible, on annule l'étape par sécurité.
            return False
        
    # def _repair_json(self, raw: str) -> str:
    #     """Corrige les erreurs d'échappement courantes dans les JSON générés par le LLM."""
    #     # Problème connu : \' n'est pas valide en JSON
    #     raw = raw.replace("\\'", "'")
    #     # Autres corrections potentielles à ajouter ici
    #     return raw
    
    def _interpolate_dict(self, obj, variable_registry: dict):
        """
        Parcourt récursivement un objet Python (dict/list) pour remplacer les marqueurs $@_var.
        Retourne l'objet avec les valeurs interpolées.
        """
        if isinstance(obj, dict):
            return {k: self._interpolate_dict(v, variable_registry) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._interpolate_dict(item, variable_registry) for item in obj]
        elif isinstance(obj, str):
            # Utiliser re.sub pour remplacer chaque occurrence
            def replace_var(match):
                var_name = match.group(1)
                if var_name in variable_registry:
                    # La valeur peut être de n'importe quel type (str, int, bool, dict, etc.)
                    value = variable_registry[var_name]["value"]
                    # Si c'est une chaîne, on la retourne directement (sans guillemets)
                    # Si c'est un type complexe, on le retourne tel quel (il sera sérialisé plus tard)
                    # Mais attention : si on retourne un dict ici, et qu'on est dans une string,
                    # cela va produire une chaîne comme "<dict>" ? Non, car on retourne l'objet lui-même.
                    # Mais le parcours récursif va continuer sur ce dict ? Oui, si on retourne un dict,
                    # il sera traité par le premier if. Donc on peut retourner value directement.
                    return value
                else:
                    # Variable inconnue : on laisse le marqueur (le validateur bloquera si c'est un execute_if)
                    return match.group(0)
            return re.sub(r'\$@_([a-zA-Z0-9_]+)', replace_var, obj)
        else:
            return obj
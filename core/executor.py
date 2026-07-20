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

    async def execute_plan(self, plan: Plan, current_context: str, current_attempt: PlanAttempt) -> SolverResult:
        Logger.info(f"[Executor] 🚀 Initialisation de l'exécution du plan : '{plan.goal}'")

        user_responses: List[str] = []
        executed_steps_trace: List[str] = []
        accumulated_context = current_context

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

                # OBSERVABILITY : pousser le step_id pour cette étape
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
                            "reason": step.result_context
                        })

                        failure_trace = self._build_failure_trace(
                            executed_steps_trace, step, _("Échec d'exécution de l'action"), step.result_context
                        )
                        return SolverResult(
                            status=ExecutionStatus.FAILED,
                            final_context=accumulated_context + f"\n{failure_trace}",
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
                            failure_class=FailureClass.CONVERGENCE_FAILURE,
                            target_entity="Executor"
                        )

            final_user_text = "\n\n".join([r for r in user_responses if r])
            final_user_text = self._interpolate_text(final_user_text)

            Logger.info("[Executor] 🎉 Fin de traitement : toutes les étapes ont convergé.")
            return SolverResult(
                status=ExecutionStatus.SUCCESS,
                final_context=accumulated_context,
                response=final_user_text or _("Mission [{}] accomplie.").format(self.solver.id),
                resolved_data=self.solver.variable_registry,
                failure_class=None
            )

        except Exception as e:
            Logger.error(f"[Executor] 🔥 Exception critique : {str(e)}")
            raise e

    # =====================================================
    # MÉTHODES PRIVÉES (inchangées sauf si besoin)
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
            Logger.error(f"[Executor] 🛑 Type de step non pris en charge dans _execute_step_action : '{step.type}'")
            return False, "", _("Erreur de spécification : Le type d'étape '{}' est introuvable.").format(step.type)

    def _interpolate_text(self, text: str) -> str:
        if not text:
            return text
        matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', text)
        interpolated = text
        for var_name in matches:
            if var_name in self.solver.variable_registry:
                real_value = self.solver.variable_registry[var_name]["value"]
                interpolated = interpolated.replace(f"$@_{var_name}", str(real_value))
            else:
                interpolated = interpolated.replace(f"$@_{var_name}", _("[Action ignorée]"))
        return interpolated

    async def _handle_abstract_task(self, step: PlanStep, runtime_context: str) -> Tuple[bool, str, Optional[str], Optional[SolverResult]]:
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

    async def _handle_tool_call(self, step: PlanStep) -> Tuple[bool, str, Optional[str]]:
        try:
            tool_args = json.loads(step.tool_args_json)
        except json.JSONDecodeError as e:
            error_detail = f"{e.msg} at line {e.lineno} column {e.colno} (pos {e.pos})"
            return False, "", _("Échec de désérialisation JSON initial : {}").format(error_detail)

        tool_args = self._interpolate_dict(tool_args, self.solver.variable_registry)
        hardware_result_str = await self.solver.execute_tool(step.tool_name, tool_args)

        try:
            parsed_result = json.loads(hardware_result_str)
            is_success_flag = str(parsed_result.get("result", "")).strip().lower()
            actual_data = parsed_result.get("data", None)

            if step.output_variable_name:
                base_name = step.output_variable_name
                self.solver.variable_registry[base_name] = {
                    "value": is_success_flag,
                    "description": step.output_variable_desc or _("Statut de l'opération {}").format(step.tool_name),
                    "source": self.solver.id,
                    "timestamp": datetime.now().isoformat()
                }
                if actual_data is not None:
                    data_var_name = base_name + "_data"
                    self.solver.variable_registry[data_var_name] = {
                        "value": actual_data,
                        "description": step.output_variable_desc or _("Données retournées par {}").format(step.tool_name),
                        "source": self.solver.id,
                        "timestamp": datetime.now().isoformat()
                    }

            return True, is_success_flag, None

        except json.JSONDecodeError:
            return False, "", "Le retour de l'outil C++ ne respecte pas le format JSON strict."

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
        expr = expr.replace('!=', '___NEQ___')
        expr = expr.replace('&&', ' and ')
        expr = expr.replace('||', ' or ')
        expr = expr.replace('!', ' not ')
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

        interpolated = self._interpolate_text(condition_raw).strip()
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

    def _interpolate_dict(self, obj, variable_registry: dict):
        if isinstance(obj, dict):
            return {k: self._interpolate_dict(v, variable_registry) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._interpolate_dict(item, variable_registry) for item in obj]
        elif isinstance(obj, str):
            def replace_var(match):
                var_name = match.group(1)
                if var_name in variable_registry:
                    return variable_registry[var_name]["value"]
                else:
                    return match.group(0)
            return re.sub(r'\$@_([a-zA-Z0-9_]+)', replace_var, obj)
        else:
            return obj
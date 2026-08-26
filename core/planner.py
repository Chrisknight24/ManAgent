"""
planner.py
==========
Composant d'ingénierie de plan (Stateless).
Hérite désormais de Entity pour bénéficier de l'ID unique et des DataProviders.
Version corrigée : activation explicite de la Progressive Disclosure.
"""

import asyncio
import json
from pydantic import ValidationError
from .plan_models import Plan, PlanStep, StepType
from core.llm import Llm
from utils.logger import Logger
import re
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from typing import Tuple, List, Optional, Any, Dict
from core.entity import Entity

PLANNER_PD_ENABLED = False


class PlannerRegistryProvider:
    """
    DataProvider pour le Planner – interroge le registre du Solver parent
    via le runtime_state.execution_context.
    """

    def __init__(self, planner: 'Planner'):
        self.planner = planner

    def get_data_type(self) -> str:
        return "registry"

    def get_targets(self) -> List[str]:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return []
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        return list(registry.keys()) if registry else []

    def get_data(self, target: str) -> Any:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return None
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        return registry.get(target, {}).get("value")

    def get_metadata(self, target: str) -> Dict[str, Any]:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return {}
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        info = registry.get(target, {})
        return {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A")
        }


class Planner(Entity):
    """
    Planner – construit des plans d'action.
    Hérite de Entity.
    """

    def __init__(
        self,
        name: str,
        llm: Llm,
        runtime_state,
        parent: Optional[Entity] = None
    ):
        super().__init__(name=name, role="planner", llm=llm, parent=parent)
        self.runtime_state = runtime_state
        self._cached_advice: Optional[str] = None
        self._last_proposed_plan: Optional[Plan] = None

        if self.runtime_state.discovery_engine:
            registry_provider = PlannerRegistryProvider(self)
            self.register_data_provider("registry", registry_provider)
            Logger.info(
                _("[Planner:{name}] DataProvider 'registry' enregistré (via contexte d'exécution).")
                .format(name=self.name)
            )

            if PLANNER_PD_ENABLED:
                if not self.llm._discovery_enabled:
                    self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                    Logger.info(
                        _("[Planner:{name}] Progressive Disclosure activée.")
                        .format(name=self.name)
                    )
            else:
                Logger.debug(
                    f"[Planner:{self.name}] Progressive Disclosure désactivée pour cette entité "
                    f"(PLANNER_PD_ENABLED=False)."
                )

    async def process(self, *args, **kwargs) -> Plan:
        goal = kwargs.get("goal", args[0] if args else "")
        context = kwargs.get("context", args[1] if len(args) > 1 else "")
        strategy = kwargs.get("strategy", args[2] if len(args) > 2 else "")
        variable_registry = kwargs.get("variable_registry", args[3] if len(args) > 3 else {})
        return await self.propose_plan(goal, context, strategy, variable_registry)

    async def propose_plan(self, goal: str, context: str, strategy: str, variable_registry: dict) -> Plan:
        Logger.info("[Planner] 🧠 Traduction de la stratégie en plan d'action structuré...")

        fallback_msg = "Aucun conseil historique ou sémantique pertinent disponible pour cette tâche."
        if self._cached_advice is None:
            self._cached_advice = fallback_msg
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    advice_result = await self.runtime_state.learner.get_advice(
                        entity_types=["Planner", "Executor"], goal=goal
                    )
                    if advice_result:
                        self._cached_advice = advice_result
                except Exception as e:
                    Logger.error(f"[Planner] Erreur récupération des conseils : {e}")
                    self._cached_advice = fallback_msg
        advice = self._cached_advice
        if advice:
            Logger.info(f"[Planner] 💡 Conseil injecté ({len(advice)} caractères).")

        tools_view = await self.runtime_state.tools_manager.get_tools_view()
        loader = get_prompt_loader()

        enriched_registry = {}
        for name, info in (variable_registry or {}).items():
            enriched_registry[name] = {
                "description": info.get("description", ""),
                "timestamp": info.get("timestamp", "N/A"),
                "source": info.get("source", "N/A"),
            }

        prompt = loader.load(
            "planner.md",
            lang=self.runtime_state.language,
            goal=goal,
            context=context,
            strategy=strategy,
            variable_registry=enriched_registry,
            tools=tools_view,
            advice=advice
        )

        proposed_plan: Plan = await self.llm.generate_structured(
            prompt=prompt,
            schema=Plan,
            tag="Plan",
            with_discovery=False
        )

        try:
            plan_json = proposed_plan.model_dump_json(indent=2)
            Logger.debug(f"[Planner] Plan généré :\n{plan_json}")
        except Exception as e:
            Logger.warning(f"[Planner] Impossible de logger le plan : {e}")

        self._last_proposed_plan = proposed_plan
        if not proposed_plan.steps:
            raise ValueError(_("Le plan généré par le LLM est structurellement valide mais ne contient aucune étape."))

        Logger.info(f"[Planner] ✅ Plan structuré reçu avec {len(proposed_plan.steps)} étapes.")

        is_valid, warnings = self._validate_plan(proposed_plan, variable_registry)
        if not is_valid:
            raise ValueError(_("Plan invalide :\n") + "\n".join(warnings))
        elif warnings:
            Logger.warning(f"[Planner] ⚠️ Plan valide avec warnings : {', '.join(warnings)}")

        return proposed_plan

    def _validate_plan(self, plan: Plan, variable_registry: dict = None) -> Tuple[bool, List[str]]:
        known_vars = set(variable_registry.keys()) if variable_registry else set()
        # Normalisation symétrique pour les variables héritées
        for var in list(known_vars):
            if var.startswith("bool_"):
                known_vars.add("data_" + var[5:])
            elif var.startswith("data_"):
                known_vars.add("bool_" + var[5:])

        errors = []
        warnings = []
        all_steps_by_id = {step.id: step for step in plan.steps}
        steps_seen_so_far = set()

        for step_idx, step in enumerate(plan.steps):
            # 1. Collecter les variables utilisées dans cette étape
            used_in_this_step = set()
            for field in [step.execute_if, step.response_text, step.tool_args_json, step.step_context, step.description]:
                if field:
                    matches = re.findall(r'(?:\$@_|@\$_)([a-zA-Z0-9_]+)', str(field))
                    used_in_this_step.update(matches)

            # 2. Vérification de disponibilité / causalité
            for var in used_in_this_step:
                is_available = False
                if var in known_vars:
                    is_available = True
                elif var.startswith("data_") and ("bool_" + var[5:]) in known_vars:
                    is_available = True
                elif var.startswith("bool_") and ("data_" + var[5:]) in known_vars:
                    is_available = True

                if not is_available:
                    # Vérifier si la variable fait référence à une étape connue
                    matched_step_id = None
                    if var.startswith("data_") or var.startswith("bool_"):
                        cand_id = var[5:]
                        if cand_id in all_steps_by_id:
                            matched_step_id = cand_id

                    # Vérifier si elle correspond à l'output_variable_name d'une étape future
                    future_step = None
                    for future_idx in range(step_idx + 1, len(plan.steps)):
                        cand_step = plan.steps[future_idx]
                        if cand_step.output_variable_name:
                            out_name = cand_step.output_variable_name
                            if var == out_name or (out_name.startswith("bool_") and var == "data_" + out_name[5:]) or (out_name.startswith("data_") and var == "bool_" + out_name[5:]):
                                future_step = cand_step
                                break

                    if matched_step_id:
                        target_step = all_steps_by_id[matched_step_id]
                        if target_step.type == StepType.DIRECT_ANSWER:
                            errors.append(
                                _("L'étape '{}' tente d'utiliser la variable '{}' associée à l'étape '{}' de type 'direct_answer' (les réponses directes ne produisent pas de données pour d'autres étapes).")
                                .format(step.id, var, matched_step_id)
                            )
                        elif matched_step_id not in steps_seen_so_far:
                            errors.append(
                                _("L'étape '{}' tente d'utiliser la variable '{}' provenant de l'étape future '{}' (erreur de causalité : l'étape n'a pas encore été exécutée).")
                                .format(step.id, var, matched_step_id)
                            )
                        else:
                            errors.append(
                                _("L'étape '{}' tente d'utiliser la variable '{}' issue de l'étape '{}' qui n'a pas produit de données valides.")
                                .format(step.id, var, matched_step_id)
                            )
                    elif future_step:
                        errors.append(
                            _("L'étape '{}' tente d'utiliser la variable '{}' issue de l'étape future '{}' (erreur de causalité temporelle).")
                            .format(step.id, var, future_step.id)
                        )
                    else:
                        errors.append(
                            _("L'étape '{}' tente d'utiliser la variable inconnue '{}' qui n'a été produite par aucune étape antérieure ni par le contexte.")
                            .format(step.id, var)
                        )

            # 3. Enregistrer les variables produites par cette étape
            steps_seen_so_far.add(step.id)

            if step.type in [StepType.TOOL_CALL, StepType.ABSTRACT_TASK]:
                known_vars.add(f"bool_{step.id}")
                known_vars.add(f"data_{step.id}")

            if step.output_variable_name:
                out_name = step.output_variable_name
                known_vars.add(out_name)
                if out_name.startswith("bool_"):
                    known_vars.add("data_" + out_name[5:])
                elif out_name.startswith("data_"):
                    known_vars.add("bool_" + out_name[5:])
                else:
                    known_vars.add("data_" + out_name)

            # 4. Vérifications d'intégrité tool_args_json
            if step.type == StepType.TOOL_CALL and step.tool_args_json:
                try:
                    args = json.loads(step.tool_args_json)
                    if isinstance(args, dict):
                        forbidden_keys = ["output_variable_name", "output_var", "var_name", "variable_name"]
                        for key in forbidden_keys:
                            if key in args:
                                errors.append(
                                    _("L'étape '{}' définit '{}' dans tool_args_json, ce qui est interdit. "
                                    "Utilisez uniquement le champ output_variable_name de l'étape pour nommer des variables.")
                                    .format(step.id, key)
                                )
                                break
                except (json.JSONDecodeError, TypeError):
                    pass

        if not errors:
            return True, warnings
        return False, errors

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
        # Appel au parent (Entity)
        super().__init__(name=name, role="planner", llm=llm, parent=parent)
        self.runtime_state = runtime_state
        self._cached_advice: Optional[str] = None
        self._last_proposed_plan: Optional[Plan] = None

        # --- Enregistrement du DataProvider pour le registre ---
        if self.runtime_state.discovery_engine:
            registry_provider = PlannerRegistryProvider(self)
            self.register_data_provider("registry", registry_provider)
            Logger.info(
                _("[Planner:{name}] DataProvider 'registry' enregistré (via contexte d'exécution).")
                .format(name=self.name)
            )

            # --- Activation explicite de la Progressive Disclosure ---
            if not self.llm._discovery_enabled:
                self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                Logger.info(
                    _("[Planner:{name}] Progressive Disclosure activée.")
                    .format(name=self.name)
                )

    async def process(self, *args, **kwargs) -> Plan:
        goal = kwargs.get("goal", args[0] if args else "")
        context = kwargs.get("context", args[1] if len(args) > 1 else "")
        strategy = kwargs.get("strategy", args[2] if len(args) > 2 else "")
        variable_registry = kwargs.get("variable_registry", args[3] if len(args) > 3 else {})
        return await self.propose_plan(goal, context, strategy, variable_registry)

    async def propose_plan(self, goal: str, context: str, strategy: str, variable_registry: dict) -> Plan:
        Logger.info("[Planner] 🧠 Traduction de la stratégie en plan d'action structuré...")

        if self._cached_advice is None:
            self._cached_advice = ""
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    self._cached_advice = await self.runtime_state.learner.get_advice(
                        entity_types=["Planner", "Executor"], goal=goal
                    )
                except Exception as e:
                    Logger.error(f"[Planner] Erreur récupération des conseils : {e}")
                    self._cached_advice = ""
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
            mission_id=self.runtime_state.current_mission_id
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
        created_vars = set(variable_registry.keys()) if variable_registry else set()
        used_vars = set()
        errors = []
        warnings = []

        # 1. Ajouter les variables créées par output_variable_name des étapes
        for step in plan.steps:
            if step.output_variable_name:
                created_vars.add(step.output_variable_name)
                # Convention : bool_xxx => data_xxx automatique
                if step.output_variable_name.startswith("bool_"):
                    created_vars.add("data_" + step.output_variable_name[5:])

        # 2. Collecter toutes les utilisations de variables
        for step in plan.steps:
            for field in [step.execute_if, step.response_text, step.tool_args_json]:
                if field:
                    matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', field)
                    used_vars.update(matches)

        # 3. Vérifier les variables utilisées mais jamais créées
        unknown = set()
        for var in used_vars:
            if var in created_vars:
                continue
            # Si la variable est de type "data_*", on accepte si le "bool_*" correspondant existe
            if var.startswith("data_"):
                bool_var = "bool_" + var[5:]
                if bool_var in created_vars:
                    continue
            unknown.add(var)

        if unknown:
            errors.append(_("Variables utilisées mais jamais créées : {}").format(', '.join(unknown)))

        # 4. Variables créées mais jamais utilisées (sauf héritées)
        inherited_vars = set(variable_registry.keys()) if variable_registry else set()
        unused_plan_vars = (created_vars - used_vars) - inherited_vars
        # Filtrer les data_* qui ont leur bool_* utilisé
        filtered_unused = set()
        for var in unused_plan_vars:
            if var.startswith("data_"):
                bool_var = "bool_" + var[5:]
                if bool_var in used_vars or bool_var in inherited_vars:
                    continue
            filtered_unused.add(var)

        if filtered_unused:
            warnings.append(_("Variables créées dans le plan mais jamais utilisées : {}").format(', '.join(filtered_unused)))

        # 5. Vérifications spécifiques aux étapes
        for step in plan.steps:
            # 5a. expected_result="any" => output_variable_name obligatoire
            if step.type == StepType.TOOL_CALL and step.expected_result == "any":
                if not step.output_variable_name:
                    errors.append(_("L'étape '{}' a expected_result='any' mais ne définit aucun output_variable_name.").format(step.id))

            # 5b. INTERDICTION : définir un output_variable_name dans tool_args_json
            if step.type == StepType.TOOL_CALL and step.tool_args_json:
                try:
                    args = json.loads(step.tool_args_json)
                    forbidden_keys = ["output_variable_name", "output_var", "var_name", "variable_name"]
                    for key in forbidden_keys:
                        if key in args:
                            errors.append(
                                _("L'étape '{}' définit '{}' dans tool_args_json, ce qui est interdit. "
                                "Utilisez uniquement le champ output_variable_name de l'étape pour créer des variables.")
                                .format(step.id, key)
                            )
                            break
                except (json.JSONDecodeError, TypeError):
                    pass

        if not errors and not warnings:
            return True, []
        if not errors:
            return True, warnings
        return False, errors
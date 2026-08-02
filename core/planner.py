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
        """
        Implémentation de la méthode abstraite de Entity.
        Délègue à propose_plan.
        """
        goal = kwargs.get("goal", args[0] if args else "")
        context = kwargs.get("context", args[1] if len(args) > 1 else "")
        strategy = kwargs.get("strategy", args[2] if len(args) > 2 else "")
        variable_registry = kwargs.get("variable_registry", args[3] if len(args) > 3 else {})
        return await self.propose_plan(goal, context, strategy, variable_registry)

    async def propose_plan(self, goal: str, context: str, strategy: str, variable_registry: dict) -> Plan:
        """Construit un plan à partir de la stratégie."""
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

        for step in plan.steps:
            if step.output_variable_name:
                created_vars.add(step.output_variable_name)

        for step in plan.steps:
            for field in [step.execute_if, step.response_text, step.tool_args_json]:
                if field:
                    matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', field)
                    used_vars.update(matches)

        filtered_used = set()
        for var in used_vars:
            if var.endswith("_data"):
                base_var = var[:-5]
                if base_var in created_vars:
                    continue
            filtered_used.add(var)

        unknown = filtered_used - created_vars
        if unknown:
            errors.append(_("Variables utilisées mais jamais créées : {}").format(', '.join(unknown)))

        inherited_vars = set(variable_registry.keys()) if variable_registry else set()
        unused_plan_vars = (created_vars - filtered_used) - inherited_vars
        if unused_plan_vars:
            warnings.append(_("Variables créées dans le plan mais jamais utilisées : {}").format(', '.join(unused_plan_vars)))

        for step in plan.steps:
            if step.type == StepType.TOOL_CALL and step.expected_result == "any":
                if not step.output_variable_name:
                    errors.append(_("L'étape '{}' a expected_result='any' mais ne définit aucun output_variable_name.").format(step.id))

        if not errors and not warnings:
            return True, []
        if not errors:
            return True, warnings
        return False, errors
"""
planner.py
==========
Composant d'ingénierie de plan (Stateless).
Responsable de la génération du graphe d'actions structuré conditionnel (DAG Plat).
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
from typing import Tuple, List, Optional

class Planner:
    """
    Composant d'ingénierie de plan (Stateless).
    Responsable de la génération du graphe d'actions structuré (HTN / DAG).
    """

    def __init__(self, llm: Llm, runtime_state):
        self.llm = llm
        self.runtime_state = runtime_state
        # Cache l'avis du reranker pour la durée de vie de CE Planner (= durée de vie du Solver
        # qui l'a créé, voir solver.py). Le goal ne change pas entre deux tentatives de
        # replanification (max_tries) : interroger le reranker LLM à chaque retry serait un appel
        # gaspillé, en plus d'aggraver la dépendance aux quotas API déjà tendue sur ce projet.
        # None = "pas encore interrogé" ; "" = "interrogé, rien d'applicable" (état légitime, pas
        # à reconfondre avec None, d'où l'usage explicite de None comme sentinelle).
        self._cached_advice: Optional[str] = None

    async def propose_plan(self, goal: str, context: str, strategy: str, variable_registry: dict) -> Plan:
        Logger.info("[Planner] 🧠 Traduction de la stratégie en plan d'action structuré...")

        # --- RAG v2 : un seul appel au reranker LLM, mis en cache pour ce Planner ---
        # On interroge Planner ET Executor en une fois : l'Executor n'a jamais la main pour agir
        # sur ses propres leçons (il exécute ce que le plan lui dit, il ne décide de rien — voir
        # ENTITY_MANIFEST), donc c'est bien le Planner qui doit voir les DEUX catégories pour
        # pouvoir, par exemple, ajouter une re-tentative ou un changement de stratégie en réponse
        # à une leçon sur la fiabilité d'un outil.
        if self._cached_advice is None:
            self._cached_advice = ""
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    self._cached_advice = await self.runtime_state.learner.get_advice(
                        entity_types=["Planner", "Executor"], goal=goal
                    )
                except Exception as e:
                    Logger.error(f"[Planner] Erreur récupération des conseils (reranker) : {e}")
                    self._cached_advice = ""
        advice = self._cached_advice
        if advice:
            Logger.info(f"[Planner] 💡 Conseil injecté dans le prompt ({len(advice)} caractères).")
        else:
            Logger.debug("[Planner] Aucun conseil injecté.")

        # Charger le template planner.md
        tools_view = await self.runtime_state.tools_manager.get_tools_view()
        loader = get_prompt_loader()
        prompt = loader.load(
            "planner.md",
            lang=self.runtime_state.language,
            goal=goal,
            context=context,
            strategy=strategy,
            variable_registry=variable_registry,
            tools=tools_view,
            advice=advice  # <--- conseil RAG + production fusionné
        )    
        proposed_plan: Plan = await self.llm.generate_structured(
            prompt=prompt,
            schema=Plan
        )
        
        if not proposed_plan.steps:
            raise ValueError(_("Le plan généré par le LLM est structurellement valide mais ne contient aucune étape."))
            
        Logger.info(f"[Planner] ✅ Plan structuré reçu avec {len(proposed_plan.steps)} étapes.")

        # --- NOUVEAU : Validation statique avec variable_registry ---
        is_valid, warnings = self._validate_plan(proposed_plan, variable_registry)
        if not is_valid:
            # On lève une exception avec les erreurs
            raise ValueError(_("Plan invalide :\n") + "\n".join(warnings))
        elif warnings:
            # On logge les warnings mais on ne bloque pas
            Logger.warning(f"[Planner] ⚠️ Plan valide avec warnings : {', '.join(warnings)}")

        return proposed_plan

    def _validate_plan(self, plan: Plan, variable_registry: dict = None) -> Tuple[bool, List[str]]:
        """
        Validation statique du plan.
        Retourne (est_valide, liste_des_warnings)
        """
        # Initialisation avec les variables héritées du registre parent
        created_vars = set(variable_registry.keys()) if variable_registry else set()
        used_vars = set()
        errors = []
        warnings = []

        # 1. Collecte des variables déclarées dans le plan
        for step in plan.steps:
            if step.output_variable_name:
                created_vars.add(step.output_variable_name)

        # 2. Collecte des variables utilisées (execute_if, response_text, tool_args_json)
        for step in plan.steps:
            for field in [step.execute_if, step.response_text, step.tool_args_json]:
                if field:
                    matches = re.findall(r'\$@_([a-zA-Z0-9_]+)', field)
                    used_vars.update(matches)

        # 3. Variables utilisées mais jamais créées → erreur bloquante
        unknown = used_vars - created_vars
        if unknown:
            errors.append(_("Variables utilisées mais jamais créées : {}").format(', '.join(unknown)))

        # 4. Variables créées mais jamais utilisées → warning
        unused = created_vars - used_vars
        # On exclut les variables héritées du registre (on ne peut pas les supprimer)
        # Pour cela, on filtre les variables qui ne viennent pas du registre
        inherited_vars = set(variable_registry.keys()) if variable_registry else set()
        unused_plan_vars = unused - inherited_vars
        if unused_plan_vars:
            warnings.append(_("Variables créées dans le plan mais jamais utilisées : {}").format(', '.join(unused_plan_vars)))

        # 5. any sans output_variable_name → erreur bloquante
        for step in plan.steps:
            if step.type == StepType.TOOL_CALL and step.expected_result == "any":
                if not step.output_variable_name:
                    errors.append(_("L'étape '{}' a expected_result='any' mais ne définit aucun output_variable_name.").format(step.id))

        return len(errors) == 0, warnings
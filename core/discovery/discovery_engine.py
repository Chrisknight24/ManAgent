"""
core/discovery/discovery_engine.py
==================================
Moteur principal du Discovery Framework.
Version corrigée : register_explorer accepte un LLM optionnel.
Support multi‑cibles : plus de référence à target unique.
"""

from typing import Dict, List, Optional, Any
from core.runtime_state import RuntimeState
from core.discovery.base_explorer import BaseExplorer
from core.discovery.discovery_session import DiscoverySession
from core.discovery.models import DiscoveryPlan, RefinedContext, ExitPolicy
from core.llm import Llm
from utils.logger import Logger
from core.cache import CacheManager
from core.constants import Events
from core.i18n import _
from core.discovery.data_provider import DataProvider


class DiscoveryEngine:
    """Moteur central du Discovery Framework."""

    def __init__(self, runtime_state: RuntimeState):
        self.runtime_state = runtime_state
        self._explorers: Dict[str, BaseExplorer] = {}
        self._cache_manager = runtime_state.cache_manager or CacheManager()
        self._cache_type = "refined_context"

    def register_explorer(self, explorer: BaseExplorer, llm: Optional[Llm] = None) -> None:
        data_type = explorer.get_data_type()
        if data_type in self._explorers:
            Logger.warning(f"[DiscoveryEngine] {_('Explorer déjà enregistré pour')} {data_type}")
            return
        
        # Assigner le LLM à l'Explorer
        if llm is not None:
            explorer.llm = llm
        elif self.runtime_state.discovery_llm is not None:
            explorer.llm = self.runtime_state.discovery_llm
        else:
            Logger.warning(f"[DiscoveryEngine] Aucun LLM fourni pour l'Explorer '{data_type}'. generate_plan échouera.")

        self._explorers[data_type] = explorer

        # Enregistrer les outils de l'Explorer dans le ToolsManager
        tools_manager = self.runtime_state.tools_manager
        if tools_manager:
            for tool_desc in explorer.get_tools_description():
                tool_name = tool_desc["name"]
                internal_name = f"discovery_{data_type}_{tool_name}"
                tools_manager.register_tool(
                    name=internal_name,
                    role=f"Discovery - {data_type}",
                    description=tool_desc["description"],
                    parameters_schema=tool_desc.get("parameters", {}),
                    source="internal"
                )
            Logger.info(f"[DiscoveryEngine] Outils de l'Explorer '{data_type}' enregistrés dans ToolsManager.")

        Logger.info(f"[DiscoveryEngine] Explorer enregistré : {data_type}")

    def get_explorer(self, data_type: str) -> Optional[BaseExplorer]:
        return self._explorers.get(data_type)

    def get_available_goals(self, data_type: str) -> List[str]:
        explorer = self.get_explorer(data_type)
        return explorer.get_available_goals() if explorer else []

    def get_tools_description(self, data_type: str) -> List[Dict]:
        explorer = self.get_explorer(data_type)
        return explorer.get_tools_description() if explorer else []

    async def get_refined_context(self, signature: str) -> Optional[RefinedContext]:
        params = {"signature": signature}
        cached = await self._cache_manager.get(self._cache_type, params)
        if cached:
            try:
                return RefinedContext(**cached)
            except Exception as e:
                Logger.warning(f"[DiscoveryEngine] Cache invalide pour {signature}, suppression : {e}")
                try:
                    await self._cache_manager.delete(self._cache_type, params)
                except Exception:
                    pass
                return None
        return None

    async def store_refined_context(self, refined: RefinedContext) -> None:
        params = {"signature": refined.signature}
        await self._cache_manager.set(
            self._cache_type,
            params,
            refined.model_dump(mode='json'),
            invalidation_markers=[refined.signature]
        )
        Logger.debug(f"[DiscoveryEngine] {_('RefinedContext stocké')} : {refined.signature}")

    async def start_discovery(
        self,
        entity_id: str,
        plan: DiscoveryPlan,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None
    ) -> RefinedContext:
        if not plan.signature:
            explorer = self.get_explorer(plan.data_type)
            if not explorer:
                raise ValueError(f"{_('Aucun Explorer pour le type')} '{plan.data_type}'")
            plan.signature = explorer.create_signature(plan.targets, plan.technical_goals)

        cached = await self.get_refined_context(plan.signature)
        if cached:
            Logger.info(f"[DiscoveryEngine] {_('Cache hit pour')} {plan.signature}")
            Logger.event(Events.DISCOVERY_CACHE_HIT, signature=plan.signature, entity_id=entity_id)
            await self._emit_discovery_events_from_cache(entity_id, plan, cached)
            return cached

        explorer = self.get_explorer(plan.data_type)
        if not explorer:
            raise ValueError(f"{_('Aucun Explorer pour le type')} '{plan.data_type}'")

        # Validation des cibles (toutes les cibles doivent être valides)
        if data_provider:
            for target in plan.targets:
                if not explorer.validate_target(target, data_provider):
                    raise ValueError(
                        _("Cible '{target}' invalide pour {data_type} (cibles disponibles : {targets})")
                        .format(target=target, data_type=plan.data_type, targets=", ".join(data_provider.get_targets()))
                    )
        else:
            for target in plan.targets:
                if not explorer.validate_target(target):
                    raise ValueError(
                        _("Cible '{target}' invalide pour {data_type}")
                        .format(target=target, data_type=plan.data_type)
                    )

        session = DiscoverySession(
            entity_id=entity_id,
            plan=plan,
            explorer=explorer,
            runtime_state=self.runtime_state,
            llm=llm
        )

        refined = await session.run(data_context=data_context)

        # On ne cache PAS les requêtes qui contiennent "last_mission" (pour éviter l'obsolescence)
        should_cache = True
        if "last_mission" in plan.targets:
            should_cache = False

        if refined.exit_policy not in (ExitPolicy.TOOL_FAILED, ExitPolicy.INVALID_PLAN) and should_cache:
            await self.store_refined_context(refined)

        return refined

    async def _emit_discovery_events_from_cache(self, entity_id: str, plan: DiscoveryPlan, refined: RefinedContext) -> None:
        session_id = plan.signature
        exec_ctx = getattr(self.runtime_state, 'execution_context', {})
        solver_id = exec_ctx.get("solver_id")
        attempt_number = exec_ctx.get("attempt_number")
        step_id = exec_ctx.get("step_id")
        mission_id = getattr(self.runtime_state, 'current_mission_id', None)

        Logger.event(Events.DISCOVERY_SESSION_START, **{
            "session_id": session_id,
            "entity_id": entity_id,
            "goal": plan.goal,
            "data_type": plan.data_type,
            "targets": plan.targets,
            "technical_goals": plan.technical_goals,
            "max_iterations": 10,
            "cache_hit": True,
            "solver_id": solver_id,
            "attempt_number": attempt_number,
            "step_id": step_id,
            "mission_id": mission_id
        })

        for entry in refined.entries:
            step_type = "tool" if entry.tool_name else "semantic"
            result = entry.tool_result or {"success": True, "data": entry.answer}
            Logger.event(Events.DISCOVERY_STEP, **{
                "session_id": session_id,
                "step_id": entry.step_id,
                "step_type": step_type,
                "description": entry.question,
                "tool_name": entry.tool_name,
                "result": result,
                "cache_hit": True
            })

        Logger.event(Events.DISCOVERY_SESSION_END, **{
            "session_id": session_id,
            "exit_policy": refined.exit_policy.value,
            "summary": refined.summary,
            "entries_count": len(refined.entries),
            "cache_hit": True,
            "solver_id": solver_id,
            "attempt_number": attempt_number,
            "step_id": step_id,
            "mission_id": mission_id
        })

    async def execute_discovery_request(
        self,
        entity_id: str,
        data_type: str,
        target: str,
        goal: str,
        question: str,
        tools: Optional[List[Dict]] = None,
        llm: Optional[Llm] = None,
        data_context: Optional[Any] = None
    ) -> RefinedContext:
        explorer = self.get_explorer(data_type)
        if not explorer:
            raise ValueError(f"{_('Aucun Explorer pour le type')} '{data_type}'")

        if goal not in explorer.get_available_goals():
            raise ValueError(f"{_('Goal')} '{goal}' {_('non disponible pour')} {data_type}")

        steps = []
        if tools:
            for tool in tools:
                steps.append({
                    "type": "tool",
                    "description": _("Appel de {tool}").format(tool=tool.get("name")),
                    "tool_name": tool.get("name"),
                    "tool_args": tool.get("args", {}),
                    "expected_result": "true"
                })

        steps.append({
            "type": "semantic",
            "description": _("Synthèse des résultats"),
            "question": question,
            "expected_result": "true"
        })

        # Construction d'un plan avec les listes
        plan = DiscoveryPlan(
            goal=goal,
            steps=steps,
            data_type=data_type,
            targets=[target],
            technical_goals=[goal],
            signature=None
        )

        return await self.start_discovery(entity_id, plan, llm=llm, data_context=data_context)
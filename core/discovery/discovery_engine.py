"""
core/discovery/discovery_engine.py
==================================
Moteur principal du Discovery Framework.
Version corrigée : register_explorer accepte un LLM optionnel.
Support multi‑cibles : plus de référence à target unique.
"""

import uuid
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

        # On n'enregistre PAS les outils de l'Explorer dans le ToolsManager
        # pour éviter que le Planner puisse les appeler directement.
        # Les outils de Discovery sont uniquement utilisés par la Progressive Disclosure.

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
        data_context: Optional[Any] = None,
        run_id: Optional[str] = None,
        entity_name: Optional[str] = None,
        entity_role: Optional[str] = None,
    ) -> RefinedContext:
        explorer = self.get_explorer(plan.data_type)
        if not explorer:
            raise ValueError(f"{_('Aucun Explorer pour le type')} '{plan.data_type}'")

        if not plan.signature:
            plan.signature = explorer.create_signature(plan.targets, plan.technical_goals)

        # Décision de cache CALCULÉE TÔT, et appliquée aux DEUX côtés
        # (lecture ET écriture) — pas seulement à l'écriture comme avant.
        # Pourquoi : la signature (data_type://target/technical_goal) ne
        # contient PAS le texte libre de la question (plan.goal). Pour un
        # technical_goal "déterministe" (ex: get_mission_details : renvoie
        # toujours le même JSON pour une cible donnée, quelle que soit la
        # formulation), c'est sans risque. Mais pour un technical_goal
        # "sémantique" (ex: analyze_execution_tree, analyze_registry : le
        # résultat EST une réponse LLM à la question posée), deux questions
        # différentes sur la même cible produisent la même signature — un
        # cache hit aurait alors servi la réponse à une AUTRE question,
        # silencieusement. Chaque Explorer déclare donc lui-même quels
        # goals ne sont jamais sûrs à mettre en cache (get_non_cacheable_goals).
        should_cache = True
        if "last_mission" in plan.targets:
            should_cache = False
        non_cacheable_goals = set(explorer.get_non_cacheable_goals()) if hasattr(explorer, "get_non_cacheable_goals") else set()
        if non_cacheable_goals.intersection(plan.technical_goals):
            should_cache = False

        exec_ctx = getattr(self.runtime_state, 'execution_context', None)

        # Identité de CETTE exécution : priorité au paramètre explicite (le cas
        # normal, posé par l'appelant via Llm._execute_discovery), sinon on
        # récupère celle déjà présente dans le scope ambiant, sinon on en crée
        # une nouvelle. Dans tous les cas, on rouvre un scope avec cette
        # valeur pour que TOUT ce qui se passe pendant cette exécution
        # (génération de plan si pas déjà fait par l'appelant, steps, appels
        # LLM/outils imbriqués) hérite automatiquement du même identifiant,
        # même si l'appelant n'a pas ouvert son propre scope.
        if run_id is None and exec_ctx is not None:
            run_id = exec_ctx.get("discovery_run_id")
        if run_id is None:
            run_id = uuid.uuid4().hex
        if entity_name is None and exec_ctx is not None:
            entity_name = exec_ctx.get("entity_name")
        if entity_role is None and exec_ctx is not None:
            entity_role = exec_ctx.get("entity_role")

        scope_kwargs = {"discovery_run_id": run_id, "discovery_signature": plan.signature}
        with self.runtime_state.execution_context.scope(**scope_kwargs):
            cached = await self.get_refined_context(plan.signature) if should_cache else None
            if cached:
                Logger.info(f"[DiscoveryEngine] {_('Cache hit pour')} {plan.signature}")
                Logger.event(
                    Events.DISCOVERY_CACHE_HIT,
                    signature=plan.signature,
                    run_id=run_id,
                    entity_id=entity_id
                )
                await self._emit_discovery_events_from_cache(
                    entity_id, plan, cached, run_id=run_id,
                    entity_name=entity_name, entity_role=entity_role
                )
                return cached

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
                llm=llm,
                run_id=run_id,
                entity_name=entity_name,
                entity_role=entity_role,
            )

            refined = await session.run(data_context=data_context)

            # should_cache déjà calculé plus haut (last_mission + goals non
            # cacheables déclarés par l'Explorer) — on ne le recalcule pas
            # ici pour éviter que ce côté et le côté lecture ne redivergent.
            if refined.exit_policy not in (ExitPolicy.TOOL_FAILED, ExitPolicy.INVALID_PLAN) and should_cache:
                await self.store_refined_context(refined)

            return refined

    async def _emit_discovery_events_from_cache(
        self,
        entity_id: str,
        plan: DiscoveryPlan,
        refined: RefinedContext,
        run_id: Optional[str] = None,
        entity_name: Optional[str] = None,
        entity_role: Optional[str] = None,
    ) -> None:
        session_id = plan.signature
        exec_ctx = getattr(self.runtime_state, 'execution_context', {})
        solver_id = exec_ctx.get("solver_id")
        attempt_number = exec_ctx.get("attempt_number")
        step_id = exec_ctx.get("step_id")
        turn_id = exec_ctx.get("turn_id")
        # mission_id lu exclusivement depuis le scope ambiant (plus jamais
        # depuis l'ancien attribut sticky `current_mission_id`) : un cache
        # hit servi pendant un tour direct obtient donc bien `None`, même si
        # une mission a tourné plus tôt dans le process.
        mission_id = exec_ctx.get("mission_id")
        run_id = run_id or exec_ctx.get("discovery_run_id") or session_id
        entity_name = entity_name or exec_ctx.get("entity_name") or "unknown"
        entity_role = entity_role or exec_ctx.get("entity_role") or "unknown"

        Logger.event(Events.DISCOVERY_SESSION_START, **{
            "session_id": session_id,
            "run_id": run_id,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "entity_role": entity_role,
            "goal": plan.goal,
            "data_type": plan.data_type,
            "targets": plan.targets,
            "technical_goals": plan.technical_goals,
            "max_iterations": 10,
            "cache_hit": True,
            "solver_id": solver_id,
            "attempt_number": attempt_number,
            "step_id": step_id,
            "turn_id": turn_id,
            "mission_id": mission_id
        })

        for entry in refined.entries:
            step_type = "tool" if entry.tool_name else "semantic"
            result = entry.tool_result or {"success": True, "data": entry.answer}
            Logger.event(Events.DISCOVERY_STEP, **{
                "session_id": session_id,
                "run_id": run_id,
                "step_id": entry.step_id,
                "step_type": step_type,
                "description": entry.question,
                "tool_name": entry.tool_name,
                "result": result,
                "cache_hit": True
            })

        Logger.event(Events.DISCOVERY_SESSION_END, **{
            "session_id": session_id,
            "run_id": run_id,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "entity_role": entity_role,
            "exit_policy": refined.exit_policy.value,
            "summary": refined.summary,
            "entries_count": len(refined.entries),
            "cache_hit": True,
            "solver_id": solver_id,
            "attempt_number": attempt_number,
            "step_id": step_id,
            "turn_id": turn_id,
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
        data_context: Optional[Any] = None,
        run_id: Optional[str] = None,
        entity_name: Optional[str] = None,
        entity_role: Optional[str] = None,
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

        return await self.start_discovery(
            entity_id, plan, llm=llm, data_context=data_context,
            run_id=run_id, entity_name=entity_name, entity_role=entity_role
        )
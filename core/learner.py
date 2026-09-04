# core/learner.py
# =====================================================
# PHASE 3 – LEARNER v3 (Vectoriel + Batch Analysis + Blame Shifting)
# =====================================================

import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from core.entity import Entity
from core.llm import Llm
from utils.logger import Logger
from memory.lesson_store import LessonStore
from memory.mission_store import MissionStore
from core.execution_models import ExecutionTree, PlanAttempt, FailureClass
from core.entity_manifest import get_entity_role
from core.prompt_loader import get_prompt_loader
from core.i18n import _
import asyncio
from core.cache import CacheManager
from core.entity_learner import EntityLearner
from core.embedding_service import embed_text

# =====================================================
# MODÈLES PYDANTIC POUR L'EXTRACTION DE LEÇON
# =====================================================

class ExtractedLesson(BaseModel):
    scope: str = Field(..., description="Identité STABLE et étroite de la leçon.")
    keywords: List[str] = Field(default_factory=list, description="Mots-clés LARGES de découvrabilité.")
    recommendation: str = Field(..., description="Règle impérative et courte (1-2 phrases).")
    polarity: str = Field(..., description="avoid ou prefer")

# =====================================================
# ANALYZER (avec LLM)
# =====================================================

class Analyzer:
    """Analyse les épisodes pour en extraire des leçons via LLM."""

    def __init__(self, lesson_store: LessonStore, llm: Llm, runtime_state):
        self.lesson_store = lesson_store
        self.llm = llm
        self.runtime_state = runtime_state
        self.loader = get_prompt_loader()

    async def analyze_episode(self, episode: Dict[str, Any]) -> None:
        await self._analyze_presentator_failure(episode)

        tree_json = episode.get("execution_tree_json")
        if not tree_json or tree_json == "{}":
            return

        try:
            tree_data = json.loads(tree_json)
            tree = ExecutionTree(**tree_data)
        except Exception as e:
            Logger.error(f"[Analyzer] Erreur de désérialisation de l'arbre pour {episode.get('mission_id')}: {e}")
            return

        await self._traverse_tree(tree, episode.get("environment", "simulated"), episode.get("mission_id"))

    async def _invalidate_advisor_cache(self, scope: str) -> None:
        try:
            cache_mgr = self.runtime_state.cache_manager or CacheManager()
            await cache_mgr.invalidate([scope])
        except Exception as e:
            Logger.warning(f"[Analyzer] Échec de l'invalidation du cache Advisor : {e}")
            
    async def _analyze_presentator_failure(self, episode: Dict[str, Any]) -> None:
        raw = episode.get("presentator_result_json")
        if not raw or raw in ("{}", "null"): return
        try:
            result = json.loads(raw)
        except Exception: return
        if not result or result.get("status") != "failed": return

        error_reason = result.get("error_reason") or "Raison inconnue"
        environment = episode.get("environment", "simulated")
        mission_id = episode.get("mission_id")

        try:
            prompt = self.loader.load(
                "analyze_presentator_failure.md",
                lang=getattr(self.runtime_state, "language", "en"),
                role_description=get_entity_role("Presentator"),
                error_reason=error_reason
            )
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson, tag="ExtractedLesson"),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon Presentator : {e}")
            scope = "presentator_generation_fallback"
            recommendation = "La génération du rapport final a échoué. Vérifier la robustesse face à un contexte incomplet."
            keywords = []

        emb_text = f"{scope} {recommendation} {' '.join(keywords)}"
        embedding = await embed_text(emb_text)

        self.lesson_store.upsert_lesson(
            entity_type="Presentator", scope=scope, recommendation=recommendation,
            environment=environment, keywords=keywords, mission_id=mission_id, embedding=embedding
        )
        await self._invalidate_advisor_cache(scope)

    async def _traverse_tree(self, tree: ExecutionTree, environment: str, mission_id: Optional[str] = None) -> None:
        """
        Batch Analysis : n'analyse que le dernier échec (culmination du struggle) et l'éventuel succès qui a suivi.
        Blame Shifting : Si l'Executor échoue (mauvaise exécution d'outil), c'est la faute du Planner qui a mal paramétré.
        """
        failed_attempts = [a for a in tree.attempts if a.outcome == "failed" and a.failure_class != FailureClass.USER_CANCELLED]
        success_attempts = [a for a in tree.attempts if a.outcome == "success"]

        if failed_attempts:
            last_failed = failed_attempts[-1]
            if last_failed.target_entity in ("Executor", "Validator"):
                last_failed.target_entity = "Planner"
            await self._analyze_attempt(last_failed, tree.goal, environment, mission_id, has_previous_failure=False)
            
            if success_attempts:
                first_success = success_attempts[0]
                if first_success.target_entity in ("Executor", "Validator"):
                    first_success.target_entity = "Planner"
                await self._analyze_attempt(first_success, tree.goal, environment, mission_id, has_previous_failure=True)

        # Continuer la récursion
        for attempt in tree.attempts:
            for node in attempt.nodes:
                if node.child_execution_tree:
                    await self._traverse_tree(node.child_execution_tree, environment, mission_id)

    async def _analyze_attempt(self, attempt: PlanAttempt, goal: str, environment: str,
                               mission_id: Optional[str] = None, has_previous_failure: bool = False) -> None:
        if attempt.failure_class == FailureClass.USER_CANCELLED: return

        if attempt.outcome == "failed":
            await self._generate_avoid_lesson(attempt, goal, environment, mission_id)
            return

        if attempt.outcome == "success" and has_previous_failure:
            await self._generate_prefer_lesson(attempt, goal, environment, mission_id)
            return

    async def _generate_avoid_lesson(self, attempt: PlanAttempt, goal: str, environment: str,
                                 mission_id: Optional[str] = None) -> None:
        entity_type = attempt.target_entity
        if not entity_type: return
        if entity_type == "Validator":
            entity_type = "Planner"

        role_description = get_entity_role(entity_type)
        failure_class = attempt.failure_class
        failure_reason = attempt.failure_reason or ""

        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = self.loader.load(
                "generate_avoid_lesson.md",
                lang=getattr(self.runtime_state, "language", "en"),
                entity_type=entity_type, role_description=role_description,
                goal=goal, failure_class=failure_class.value, failure_reason=failure_reason,
                pruned_attempt=pruned
            )
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson, tag="ExtractedLesson"),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM avoid : {e}")
            scope = f"{failure_class.value}_fallback"
            recommendation = self._generate_recommendation_fallback(failure_class, failure_reason)
            keywords = []

        emb_text = f"{scope} {recommendation} {' '.join(keywords)}"
        embedding = await embed_text(emb_text)

        self.lesson_store.upsert_lesson(
            entity_type=entity_type, scope=scope, recommendation=recommendation,
            environment=environment, keywords=keywords, mission_id=mission_id,
            polarity="avoid", embedding=embedding
        )
        await self._invalidate_advisor_cache(scope)

        if failure_class in (FailureClass.EXECUTION_FAILURE, FailureClass.CONVERGENCE_FAILURE):
            for node in attempt.nodes:
                if node.status == "failed" and node.tool_name:
                    tool_scope = f"{node.tool_name}:{failure_class.value}"
                    tool_recommendation = f"Échec de l'outil {node.tool_name}. {recommendation}"
                    self.lesson_store.upsert_lesson(
                        entity_type=entity_type, scope=tool_scope, recommendation=tool_recommendation,
                        environment=environment, keywords=list(set(keywords + [node.tool_name])),
                        mission_id=mission_id, polarity="avoid", embedding=embedding
                    )
                    await self._invalidate_advisor_cache(scope)
                    break
                
    async def _generate_prefer_lesson(self, attempt: PlanAttempt, goal: str, environment: str,
                                  mission_id: Optional[str] = None) -> None:
        entity_type = attempt.target_entity or "Planner"
        if entity_type == "Validator":
            entity_type = "Planner"
        role_description = get_entity_role(entity_type)
        proposed_plan_desc = json.dumps(attempt.proposed_plan, indent=2, ensure_ascii=False) if attempt.proposed_plan else "Aucun plan stocké."

        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = self.loader.load(
                "generate_prefer_lesson.md",
                lang=getattr(self.runtime_state, "language", "en"),
                entity_type=entity_type, role_description=role_description,
                goal=goal, pruned_attempt=pruned, proposed_plan_desc=proposed_plan_desc
            )
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(prompt=prompt, schema=ExtractedLesson, tag="ExtractedLesson"),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM prefer : {e}")
            scope = "success_contrast_fallback"
            recommendation = "Privilégier une approche alternative en cas d'échec répété."
            keywords = ["alternative", "retry", "success"]

        emb_text = f"{scope} {recommendation} {' '.join(keywords)}"
        embedding = await embed_text(emb_text)

        self.lesson_store.upsert_lesson(
            entity_type=entity_type, scope=scope, recommendation=recommendation,
            environment=environment, keywords=keywords, mission_id=mission_id,
            polarity="prefer", embedding=embedding
        )
        await self._invalidate_advisor_cache(scope)
        
    def _prune_attempt_for_llm(self, attempt: PlanAttempt) -> str:
        pruned_nodes = []
        for node in attempt.nodes:
            pruned_nodes.append({
                "step_id": node.step_id,
                "description": node.description[:100],
                "expected_result": node.expected_result,
                "actual_result": node.actual_result[:100] if node.actual_result else None,
                "error_reason": node.error_reason[:100] if node.error_reason else None,
                "status": node.status
            })
        return json.dumps(pruned_nodes, indent=2, ensure_ascii=False)

    def _generate_recommendation_fallback(self, failure_class: FailureClass, failure_reason: str) -> str:
        if failure_class == FailureClass.PLAN_REJECTED_VALIDATION:
            return "Vérifiez les variables utilisées, les conditions et la syntaxe du plan généré."
        elif failure_class == FailureClass.PLAN_REJECTED_SUPERVISOR:
            return "Assurez-vous que le plan converge vers l'objectif global et respecte les contraintes."
        elif failure_class == FailureClass.EXECUTION_FAILURE:
            return "Un outil a échoué lors de l'exécution. Vérifiez les arguments passés."
        elif failure_class == FailureClass.CONVERGENCE_FAILURE:
            return "La convergence sémantique n'a pas été atteinte. Reformulez l'expected_result."
        elif failure_class == FailureClass.USER_CANCELLED:
            return "La mission a été annulée par l'utilisateur."
        elif failure_class == FailureClass.MAX_RETRIES_REACHED:
            return "Le nombre maximal de tentatives a été atteint."
        return f"Échec de classe {failure_class.value}."

# =====================================================
# ADVISOR (Recherche Vectorielle Instantanée)
# =====================================================

class Advisor:
    def __init__(self, lesson_store: LessonStore, runtime_state, llm: Llm, cache_manager: Optional[CacheManager] = None):
        if cache_manager is None:
            cache_manager = CacheManager()
        self.lesson_store = lesson_store
        self.runtime_state = runtime_state
        self.llm = llm
        self.advice_cache: Dict[str, str] = {}
        self.cache_manager = cache_manager
        
    async def prepare_advice(self, goal: str) -> None:
        advice = await self.get_advice(["Planner", "Executor"], goal)
        self.advice_cache["Planner"] = advice

    async def get_advice(self, entity_types: List[str], goal: str) -> str:
        """Recherche vectorielle instantanée des leçons pertinentes (remplace le LLM Reranker coûteux)."""
        if isinstance(entity_types, str):
            entity_types = [entity_types]
            
        effective_env = getattr(self.runtime_state, "environment", "simulated")
        
        goal_emb = None
        if hasattr(self.runtime_state, "embedding_manager") and self.runtime_state.embedding_manager:
            try:
                goal_emb = await self.runtime_state.embedding_manager.embed(goal)
            except Exception as emb_err:
                Logger.warning(f"[Advisor] Utilisation du fallback embed_text suite à : {emb_err}")
        if goal_emb is None:
            goal_emb = await embed_text(goal)
        
        selected = self.lesson_store.get_similar_lessons(goal_emb, entity_types, effective_env, top_k=3)
        
        if not selected:
            return "Aucun conseil historique ou sémantique pertinent disponible pour cette tâche."

        by_polarity = {"avoid": {}, "prefer": {}}
        for c in selected:
            polarity = c.get("polarity", "avoid")
            entity = c["entity_type"]
            if entity not in by_polarity[polarity]:
                by_polarity[polarity][entity] = []
            by_polarity[polarity][entity].append(c)

        section_titles = {
            "Planner": "Planner",
            "Executor": "Executor",
            "Solver": "Solver",
            "Presentator": "Presentator",
            "Orchestrator": "Orchestrateur",
            "Global": "Connaissances Globales & Préférences",
        }

        blocks = []
        avoid_items = by_polarity.get("avoid", {})
        if avoid_items:
            avoid_lines = ["### 🚫 À éviter (Issus d'échecs passés)"]
            for entity, lessons in avoid_items.items():
                title = section_titles.get(entity, entity)
                avoid_lines.append(f"#### {title}")
                for l in lessons:
                    created_at = l.get("created_at")
                    time_suffix = f" (enregistré le {created_at})" if created_at else ""
                    avoid_lines.append(f"- {l['recommendation']}{time_suffix}")
            blocks.append("\n".join(avoid_lines))

        prefer_items = by_polarity.get("prefer", {})
        if prefer_items:
            prefer_lines = ["### ✅ À privilégier (Approches qui ont marché & Préférences récentes)"]
            for entity, lessons in prefer_items.items():
                title = section_titles.get(entity, entity)
                prefer_lines.append(f"#### {title}")
                for l in lessons:
                    created_at = l.get("created_at")
                    time_suffix = f" (enregistré le {created_at})" if created_at else ""
                    prefer_lines.append(f"- {l['recommendation']}{time_suffix}")
            blocks.append("\n".join(prefer_lines))

        result = "\n\n".join(blocks)
        Logger.info(f"[Advisor] {len(selected)} leçon(s) injectée(s) pour {entity_types}.")
        return result

# =====================================================
# LEARNER (Entité principale)
# =====================================================

class Learner(Entity):
    def __init__(self, name: str, mission_store: MissionStore, runtime_state,
                 llm: Optional[Llm] = None, parent: Optional[Entity] = None,
                 lesson_store: Optional[LessonStore] = None):
        super().__init__(name=name, role="learner", llm=llm, parent=parent)
        self.mission_store = mission_store
        self.runtime_state = runtime_state
        self.lesson_store = lesson_store or LessonStore()
        self.analyzer = Analyzer(self.lesson_store, self.llm, self.runtime_state)
        self.advisor = Advisor(self.lesson_store, runtime_state, self.llm, cache_manager=self.runtime_state.cache_manager)
        self.entity_learner = EntityLearner(lesson_store=self.lesson_store, cache_manager=self.runtime_state.cache_manager)
        self.advice_cache: Dict[str, str] = {}

    async def process(self, command: str = "analyze", **kwargs) -> Any:
        if command == "analyze":
            return await self.analyze_all_episodes(force=kwargs.get("force", False))
        elif command == "prepare_advice":
            goal = kwargs.get("goal")
            if goal:
                await self.prepare_advice(goal)
            return None
        else:
            raise ValueError(f"Commande learner inconnue : {command}")
        
    async def consolidate_lessons(self) -> int:
        count = await self.entity_learner.consolidate_if_needed()
        if count > 0:
            Logger.info(f"[Learner] Consolidation terminée : {count} groupe(s) traités.")
        return count

    async def analyze_all_episodes(self, force: bool = False) -> int:
        if force:
            reset_count = self.mission_store.reset_analyzed()
            Logger.info(f"[Learner] Ré-analyse forcée demandée : {reset_count} épisode(s) remis à zéro.")

        episodes = self.mission_store.get_unanalyzed_episodes()
        if not episodes:
            Logger.info("[Learner] Aucun épisode non analysé.")
            return 0

        count = 0
        for episode in episodes:
            mission_id = episode.get("mission_id")
            try:
                await self.analyzer.analyze_episode(episode)
                self.mission_store.mark_analyzed(mission_id)
                count += 1
            except Exception as e:
                Logger.error(f"[Learner] Erreur analyse épisode {mission_id} : {e}")
        Logger.info(f"[Learner] Analyse terminée : {count} épisodes traités.")
        return count

    async def prepare_advice(self, goal: str) -> None:
        await self.advisor.prepare_advice(goal)
        self.advice_cache = self.advisor.advice_cache

    async def get_advice(self, entity_types: List[str], goal: str) -> str:
        return await self.advisor.get_advice(entity_types, goal)

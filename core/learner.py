# core/learner.py
# =====================================================
# PHASE 3 – LEARNER v2 (LLM + async + déduplication)
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
# =====================================================
# MODÈLES PYDANTIC POUR L'EXTRACTION DE LEÇON
# =====================================================

class ExtractedLesson(BaseModel):
    """
    Structure attendue du LLM pour une leçon.
    """
    scope: str = Field(
        ...,
        description="Identité STABLE et étroite de la leçon (clé de déduplication/evidence — "
                    "ne doit PAS varier d'un épisode à l'autre pour la même situation)."
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="Mots-clés LARGES de découvrabilité (applications, actions, synonymes, outils) "
                    "qui aideront un futur LLM à retrouver cette leçon depuis un but de mission "
                    "différent. Volontairement plus permissif que scope — pense à toutes les façons "
                    "dont un utilisateur pourrait exprimer une mission qui retomberait dans ce piège."
    )
    recommendation: str = Field(
        ...,
        description="Règle impérative et courte (1-2 phrases) pour éviter cette erreur à l'avenir."
    )
    polarity: str = Field(..., description="avoid ou prefer")


class RerankedLessons(BaseModel):
    """
    Réponse du LLM reranker : parmi les leçons candidates listées (par leur id), lesquelles
    s'appliquent réellement au but de mission courant.
    """
    relevant_lesson_ids: List[int] = Field(
        default_factory=list,
        description="IDs des leçons (parmi celles listées dans le prompt) réellement applicables "
                    "à ce but précis. Liste vide si aucune ne s'applique — ne force jamais une "
                    "correspondance approximative."
    )
    reasoning: str = Field(
        ...,
        description="Explication brève (1-2 phrases) de la sélection, ou de l'absence de sélection."
    )


# =====================================================
# ANALYZER (avec LLM)
# =====================================================

class Analyzer:
    """
    Analyse les épisodes (ExecutionTree) pour en extraire des leçons via LLM.
    """

    def __init__(self, lesson_store: LessonStore, llm: Llm, runtime_state):
        self.lesson_store = lesson_store
        self.llm = llm
        self.runtime_state = runtime_state
        self.loader = get_prompt_loader()

    async def analyze_episode(self, episode: Dict[str, Any]) -> None:
        """
        Analyse un épisode et génère des leçons (async).
        """
        # Le Presentator n'est pas dans l'ExecutionTree (il est appelé après coup par
        # l'Orchestrateur, hors de la récursion Solver/Executor) : sa télémétrie vit dans une
        # colonne séparée et doit être traitée indépendamment de la présence d'un arbre.
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

        # Parcourir l'arbre récursivement
        await self._traverse_tree(tree, episode.get("environment", "simulated"), episode.get("mission_id"))

    async def _invalidate_advisor_cache(self, scope: str) -> None:
        try:
            cache_mgr = self.runtime_state.cache_manager or CacheManager()
            await cache_mgr.invalidate([scope])
        except Exception as e:
            Logger.warning(f"[Analyzer] Échec de l'invalidation du cache Advisor : {e}")
            
    async def _analyze_presentator_failure(self, episode: Dict[str, Any]) -> None:
        """
        Analyse l'échec éventuel du Presentator pour cet épisode.
        """
        raw = episode.get("presentator_result_json")
        if not raw or raw in ("{}", "null"):
            return
        try:
            result = json.loads(raw)
        except Exception:
            return
        if not result or result.get("status") != "failed":
            return

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
            # --- ENCAPSULATION DANS LE SCOPE ---
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(
                        prompt=prompt,
                        schema=ExtractedLesson,
                        tag="ExtractedLesson"
                        # Le mission_id est désormais récupéré automatiquement via le scope
                    ),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon Presentator : {e}")
            scope = "presentator_generation_fallback"
            recommendation = (
                "La génération du rapport final a échoué. Vérifier la robustesse de "
                "generate_mission_report/generate_error_report face à un contexte incomplet ou mal formé."
            )
            keywords = []

        self.lesson_store.upsert_lesson(
            entity_type="Presentator",
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=mission_id
        )
        await self._invalidate_advisor_cache(scope)

        

    async def _traverse_tree(self, tree: ExecutionTree, environment: str, mission_id: Optional[str] = None) -> None:
        """
        Parcourt récursivement l'arbre et génère des leçons pour chaque tentative et nœud.
        """
        previous_outcomes = []
        for attempt in tree.attempts:
            has_previous_failure = "failed" in previous_outcomes
            await self._analyze_attempt(attempt, tree.goal, environment, mission_id, has_previous_failure)
            previous_outcomes.append(attempt.outcome)
            for node in attempt.nodes:
                if node.child_execution_tree:
                    await self._traverse_tree(node.child_execution_tree, environment, mission_id)

    async def _analyze_attempt(self, attempt: PlanAttempt, goal: str, environment: str,
                               mission_id: Optional[str] = None, has_previous_failure: bool = False) -> None:
        """
        Analyse une tentative et génère des leçons.
        """
        failure_class = attempt.failure_class
        failure_reason = attempt.failure_reason or ""

        if failure_class == FailureClass.USER_CANCELLED:
            return

        if attempt.outcome == "failed":
            await self._generate_avoid_lesson(attempt, goal, environment, mission_id)
            return

        if attempt.outcome == "success":
            if has_previous_failure:
                await self._generate_prefer_lesson(attempt, goal, environment, mission_id)
            return

        Logger.warning(f"[Analyzer] Outcome non reconnu : {attempt.outcome}")

    async def _generate_avoid_lesson(self, attempt: PlanAttempt, goal: str, environment: str,
                                 mission_id: Optional[str] = None) -> None:
        """
        Génère une leçon de type 'avoid' à partir d'un échec.
        """
        entity_type = attempt.target_entity
        if not entity_type:
            Logger.warning(f"[Analyzer] Attempt sans target_entity — leçon avoid ignorée.")
            return

        role_description = get_entity_role(entity_type)
        failure_class = attempt.failure_class
        failure_reason = attempt.failure_reason or ""

        scope: Optional[str] = None
        recommendation: Optional[str] = None
        keywords: List[str] = []

        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = self.loader.load(
                "generate_avoid_lesson.md",
                lang=getattr(self.runtime_state, "language", "en"),
                entity_type=entity_type,
                role_description=role_description,
                goal=goal,
                failure_class=failure_class.value,
                failure_reason=failure_reason,
                pruned_attempt=pruned
            )
            # --- ENCAPSULATION DANS LE SCOPE ---
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(
                        prompt=prompt,
                        schema=ExtractedLesson,
                        tag="ExtractedLesson"
                    ),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon avoid : {e}")
            scope = f"{failure_class.value}_fallback"
            recommendation = self._generate_recommendation_fallback(failure_class, failure_reason)
            keywords = []

        self.lesson_store.upsert_lesson(
            entity_type=entity_type,
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=mission_id,
            polarity="avoid"
        )
        await self._invalidate_advisor_cache(scope)

        # Leçon complémentaire outil (si applicable)
        if failure_class in (FailureClass.EXECUTION_FAILURE, FailureClass.CONVERGENCE_FAILURE):
            for node in attempt.nodes:
                if node.status == "failed" and node.tool_name:
                    tool_scope = f"{node.tool_name}:{failure_class.value}"
                    tool_recommendation = f"Échec de l'outil {node.tool_name}. {recommendation}"
                    self.lesson_store.upsert_lesson(
                        entity_type=entity_type,
                        scope=tool_scope,
                        recommendation=tool_recommendation,
                        environment=environment,
                        keywords=list(set(keywords + [node.tool_name])),
                        mission_id=mission_id,
                        polarity="avoid"
                    )
                    await self._invalidate_advisor_cache(scope)
                    break
                
    async def _generate_prefer_lesson(self, attempt: PlanAttempt, goal: str, environment: str,
                                  mission_id: Optional[str] = None) -> None:
        """
        Génère une leçon de type 'prefer' à partir d'un succès SURVENU APRÈS UN ÉCHEC.
        """
        entity_type = attempt.target_entity or "Planner"
        role_description = get_entity_role(entity_type)
        proposed_plan_desc = json.dumps(attempt.proposed_plan, indent=2, ensure_ascii=False) if attempt.proposed_plan else "Aucun plan stocké."

        try:
            pruned = self._prune_attempt_for_llm(attempt)
            prompt = self.loader.load(
                "generate_prefer_lesson.md",
                lang=getattr(self.runtime_state, "language", "en"),
                entity_type=entity_type,
                role_description=role_description,
                goal=goal,
                pruned_attempt=pruned,
                proposed_plan_desc=proposed_plan_desc
            )
            # --- ENCAPSULATION DANS LE SCOPE ---
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                extracted = await asyncio.wait_for(
                    self.llm.generate_structured(
                        prompt=prompt,
                        schema=ExtractedLesson,
                        tag="ExtractedLesson"
                    ),
                    timeout=30.0
                )
            scope = extracted.scope
            recommendation = extracted.recommendation
            keywords = extracted.keywords
        except Exception as e:
            Logger.error(f"[Analyzer] Échec LLM pour la leçon prefer : {e}")
            scope = "success_contrast_fallback"
            recommendation = "Lorsqu'une méthode échoue, privilégier une approche alternative (ex: utiliser la vision ou la souris) plutôt que de répéter la même action."
            keywords = ["alternative", "retry", "success"]

        self.lesson_store.upsert_lesson(
            entity_type=entity_type,
            scope=scope,
            recommendation=recommendation,
            environment=environment,
            keywords=keywords,
            mission_id=mission_id,
            polarity="prefer"
        )
        await self._invalidate_advisor_cache(scope)
        
    def _prune_attempt_for_llm(self, attempt: PlanAttempt) -> str:
        """Réduit un PlanAttempt à une séquence légère pour le LLM."""
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
        """Fallback statique en cas d'échec du LLM."""
        if failure_class == FailureClass.PLAN_REJECTED_VALIDATION:
            return "Le plan a été rejeté par validation statique. Vérifiez les variables utilisées, les conditions et la syntaxe du plan généré. Évitez les variables non déclarées et les opérateurs interdits dans les conditions."
        elif failure_class == FailureClass.PLAN_REJECTED_SUPERVISOR:
            return "Le plan a été rejeté par le superviseur. Assurez-vous que le plan converge vers l'objectif global et qu'il respecte les contraintes de l'environnement."
        elif failure_class == FailureClass.EXECUTION_FAILURE:
            if "tool" in failure_reason.lower():
                return "Un outil a échoué lors de l'exécution. Vérifiez les arguments passés à l'outil, la disponibilité de l'outil, ou utilisez 'any' comme expected_result pour éviter un rejet brutal."
            else:
                return "L'exécution a échoué pour une raison inconnue. Analysez les logs pour identifier l'étape problématique."
        elif failure_class == FailureClass.CONVERGENCE_FAILURE:
            return "La convergence sémantique n'a pas été atteinte. Le LLM a détecté une divergence entre le résultat attendu et le résultat réel. Reformulez l'expected_result ou utilisez des outils plus précis."
        elif failure_class == FailureClass.USER_CANCELLED:
            return "La mission a été annulée par l'utilisateur. Aucune action corrective n'est nécessaire, mais le système doit être prêt à reprendre."
        elif failure_class == FailureClass.MAX_RETRIES_REACHED:
            return "Le nombre maximal de tentatives a été atteint. Envisagez de simplifier le plan ou d'utiliser des outils plus robustes."
        else:
            return f"Échec de classe {failure_class.value}. Consultez les logs pour plus de détails."


# =====================================================
# ADVISOR (reranker LLM)
# =====================================================

class Advisor:
    def __init__(
        self,
        lesson_store: LessonStore,
        runtime_state,
        llm: Llm,
        cache_manager: Optional[CacheManager] = None
    ):
        if cache_manager is None:
            Logger.warning("[Advisor] Aucun cache_manager fourni, utilisation d'une instance locale (non partagée).")
            cache_manager = CacheManager()
        self.lesson_store = lesson_store
        self.runtime_state = runtime_state
        self.llm = llm
        self.advice_cache: Dict[str, str] = {}
        self.loader = get_prompt_loader()
        self.cache_manager = cache_manager
        
    async def prepare_advice(self, goal: str) -> None:
        advice = await self._llm_rerank_advice(["Planner", "Executor"], goal)
        self.advice_cache["Planner"] = advice

    async def get_advice(self, entity_types: List[str], goal: str) -> str:
        # Reranker désactivé pour l'instant.
        # if isinstance(entity_types, str):
        #     entity_types = [entity_types]
        # return await self._llm_rerank_advice(entity_types, goal)
        return ""  # <-- désactivé

    async def _llm_rerank_advice(self, entity_types: List[str], goal: str) -> str:
        effective_environment = getattr(self.runtime_state, "environment", "simulated")

        # Récupérer d'abord les leçons consolidées
        candidates = self.lesson_store.get_consolidated_lessons(entity_types, effective_environment)

        # Fallback sur les brutes si aucune consolidée
        if not candidates:
            candidates = self.lesson_store.get_active_lessons(entity_types, effective_environment)
            if candidates:
                Logger.debug(f"[Advisor] Fallback sur {len(candidates)} leçons brutes (aucune consolidée).")
            else:
                Logger.debug(f"[Advisor] Aucune leçon (consolidée ou brute) pour {entity_types}.")
                return ""
        # --- 1. Construire la clé de cache ---
        cache_params = {
            "goal": goal.strip().lower(),
            "entity_types": sorted(entity_types),
            "environment": effective_environment,
            "candidate_ids": sorted([str(c["id"]) for c in candidates])
        }

        # --- 2. Vérifier le cache ---
        cached = await self.cache_manager.get("advisor", cache_params)
        if cached is not None:
            Logger.info(f"[Advisor] Cache hit pour {entity_types} - {goal[:30]}...")
            return cached["advice"]

        # --- 3. Exécuter le reranking (code existant) ---
        strictness_note = (
            "ENVIRONNEMENT RÉEL : sois strict, ne retiens que les leçons avec une confiance et un "
            "nombre de confirmations élevés — une fausse recommandation ici a un coût réel."
            if effective_environment == "real"
            else "Environnement simulé/dev : tu peux être plus exploratoire dans ta sélection."
        )

        candidates_text = "\n".join(
            f"- id={c['id']} | entité={c['entity_type']} | scope={c['scope']} | "
            f"mots-clés={c['keywords']} | confiance={c['confidence']:.2f} | "
            f"confirmations={c['evidence_count']} | contradictions={c['contradiction_count']}\n"
            f"    recommandation: {c['recommendation']}"
            for c in candidates
        )

        lang_code = getattr(self.runtime_state, "language", "fr")

        prompt = self.loader.load(
            "advisor_rerank_advice.md",
            lang=lang_code,
            goal=goal,
            strictness_note=strictness_note,
            candidates_text=candidates_text,
            lang_code=lang_code
        )

        try:
            reranked = await asyncio.wait_for(
                self.llm.generate_structured(prompt=prompt, schema=RerankedLessons, tag="RerankedLessons"),
                timeout=35.0
            )
        except Exception as e:
            Logger.error(f"[Advisor] Échec du reranker LLM : {type(e).__name__}: {e}")
            if candidates:
                fallback_selected = candidates[:3]
                fallback_lines = ["⚠️ Reranker indisponible (timeout/erreur). Voici les conseils les plus confiants :"]
                for c in fallback_selected:
                    fallback_lines.append(f"- {c['recommendation']}")
                Logger.warning(f"[Advisor] Fallback utilisé : {len(fallback_selected)} leçon(s) retournée(s).")
                return "\n".join(fallback_lines)
            return ""

        Logger.debug(f"[Advisor] Reranker : {reranked.reasoning}")

        selected = [c for c in candidates if c["id"] in reranked.relevant_lesson_ids]
        if not selected:
            Logger.debug(f"[Advisor] Aucune leçon retenue par le reranker pour {entity_types}.")
            return ""

        # --- 4. Formater la réponse (code existant, inchangé) ---
        by_polarity: Dict[str, Dict[str, List[Dict[str, Any]]]] = {"avoid": {}, "prefer": {}}
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
        }

        blocks = []

        avoid_items = by_polarity.get("avoid", {})
        if avoid_items:
            avoid_lines = ["### 🚫 À éviter (Issus d'échecs)"]
            for entity, lessons in avoid_items.items():
                title = section_titles.get(entity, entity)
                avoid_lines.append(f"#### {title}")
                for l in lessons:
                    avoid_lines.append(f"- {l['recommendation']}")
            blocks.append("\n".join(avoid_lines))

        prefer_items = by_polarity.get("prefer", {})
        if prefer_items:
            prefer_lines = ["### ✅ À privilégier (Issus de succès après échec)"]
            for entity, lessons in prefer_items.items():
                title = section_titles.get(entity, entity)
                prefer_lines.append(f"#### {title}")
                for l in lessons:
                    prefer_lines.append(f"- {l['recommendation']}")
            blocks.append("\n".join(prefer_lines))

        if not blocks:
            return ""

        result = "\n\n".join(blocks)

        # --- 5. Stocker le résultat dans le cache ---
        markers = [str(c["id"]) for c in candidates]
        await self.cache_manager.set(
            "advisor",
            cache_params,
            {"advice": result},
            invalidation_markers=markers
        )
        Logger.debug(f"[Advisor] Résultat stocké dans le cache pour {entity_types} - {goal[:30]}...")

        Logger.info(f"[Advisor] {len(selected)} leçon(s) retenue(s) par le reranker pour {entity_types}.")
        return result

# =====================================================
# LEARNER (Entité principale)
# =====================================================

class Learner(Entity):
    """
    Entité d'apprentissage. Hérite d'Entity.
    - Analyse les épisodes non encore analysés (async).
    - Utilise le LLM pour extraire des leçons sémantiques.
    - Fournit des conseils adaptés au goal courant.
    """

    def __init__(self, name: str, mission_store: MissionStore, runtime_state,
                 llm: Optional[Llm] = None, parent: Optional[Entity] = None):
        super().__init__(name=name, role="learner", llm=llm, parent=parent)
        self.mission_store = mission_store
        self.runtime_state = runtime_state
        self.lesson_store = LessonStore()
        self.analyzer = Analyzer(self.lesson_store, self.llm, self.runtime_state)
        self.advisor = Advisor(
            self.lesson_store,
            runtime_state,
            self.llm,
            cache_manager=self.runtime_state.cache_manager  # <-- AJOUT
        )
        self.entity_learner = EntityLearner(
            lesson_store=self.lesson_store,
            cache_manager=self.runtime_state.cache_manager
        )
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
        """Déclenche la consolidation des leçons (si le seuil est atteint)."""
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
        if isinstance(entity_types, str):
            entity_types = [entity_types]
        return await self.advisor.get_advice(entity_types, goal)
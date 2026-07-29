# core/mission_compactor.py
# Version avec récupération des leçons issues des missions similaires

import time
from typing import List, Dict, Any, Optional
from core.entity import Entity
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from utils.logger import Logger
from core.plan_models import CompactedAdvice
from core.cache import CacheManager
from memory.lesson_store import LessonStore  # <-- AJOUT

class MissionCompactor(Entity):
    """
    Entité dédiée à la compaction sémantique des missions similaires.
    Utilise le LLM pour synthétiser les patterns gagnants et les écueils,
    et pour juger de la nouveauté de la mission actuelle.
    Version avec intégration des leçons associées aux missions similaires.
    """

    def __init__(
        self,
        name: str = "mission_compactor",
        parent: Optional[Entity] = None,
        cache_manager: Optional[CacheManager] = None
    ):
        if cache_manager is None:
            Logger.warning("[MissionCompactor] Aucun cache_manager fourni, utilisation d'une instance locale (non partagée).")
            cache_manager = CacheManager()
        super().__init__(name=name, role="compactor", llm=None, parent=parent)
        self.cache_manager = cache_manager
        self.lesson_store = LessonStore()  # <-- pour accéder aux leçons

    async def process(self, *args, **kwargs) -> CompactedAdvice:
        """Implémentation de la méthode abstraite de Entity."""
        goal = kwargs.get("goal", args[0] if args else "")
        similar_missions = kwargs.get("similar_missions", args[1] if len(args) > 1 else [])
        llm = kwargs.get("llm", args[2] if len(args) > 2 else None)
        runtime_state = kwargs.get("runtime_state", args[3] if len(args) > 3 else None)

        if not llm:
            raise ValueError("MissionCompactor.process() requires a 'llm' parameter.")

        return await self.compact(goal, similar_missions, llm, runtime_state)

    async def compact(
        self,
        goal: str,
        similar_missions: List[Dict[str, Any]],
        llm: Llm,
        runtime_state,
        query_signatures: Optional[List] = None
    ) -> CompactedAdvice:
        """
        Synthétise la liste des missions similaires en un conseil stratégique
        et détermine si la mission actuelle est nouvelle.
        Inclut les leçons issues des missions similaires.
        """
        # --- 1. Vérifier le cache ---
        if query_signatures and self.cache_manager:
            normalized_sigs = []
            for s in query_signatures:
                normalized_sigs.append({
                    "action": s.action.strip().lower(),
                    "object": s.object.strip().lower(),
                    "desired_state": s.desired_state.strip().lower() if s.desired_state else None
                })
            cache_params = {
                "signatures": normalized_sigs,
                "goal": goal.strip().lower()
            }
            cached = await self.cache_manager.get("compactor", cache_params)
            if cached is not None:
                Logger.info(f"[MissionCompactor] Cache hit pour {len(query_signatures)} signatures.")
                return CompactedAdvice(**cached)

        # --- 2. Si pas de cache, exécuter la compaction ---
        if not similar_missions:
            Logger.debug("[MissionCompactor] Aucune mission similaire fournie, compaction ignorée.")
            return CompactedAdvice(
                advice="Aucune mission similaire trouvée. Aucun conseil disponible.",
                is_novel=True,
                confidence=1.0
            )

        Logger.event(
            "mission_compactor_start",
            goal=goal,
            nb_missions=len(similar_missions)
        )

        start_time = time.monotonic()
        try:
            # --- 2a. Formater les missions similaires ---
            missions_text = self._format_missions(similar_missions)

            # --- 2b. Récupérer les leçons associées à ces missions ---
            mission_ids = [m.get("mission_id") for m in similar_missions if m.get("mission_id")]
            lessons_text = ""
            if mission_ids:
                lessons = self._fetch_lessons_for_missions(mission_ids)
                if lessons:
                    lessons_text = self._format_lessons(lessons)
                    Logger.debug(f"[MissionCompactor] {len(lessons)} leçons associées aux missions similaires.")
                else:
                    Logger.debug("[MissionCompactor] Aucune leçon associée aux missions similaires.")

            # --- 2c. Construire le prompt ---
            # On combine les deux sections : d'abord les missions, puis les leçons
            combined_context = missions_text
            if lessons_text:
                combined_context += f"\n\n--- LEÇONS TIRÉES DE CES MISSIONS ---\n{lessons_text}"

            loader = get_prompt_loader()
            prompt = loader.load(
                "mission_compactor.md",
                lang=runtime_state.language,
                goal=goal,
                missions=combined_context  # on remplace missions par le contexte enrichi
            )

            compacted: CompactedAdvice = await llm.generate_structured(
                prompt=prompt,
                schema=CompactedAdvice,
                tag="MissionCompactor"
            )

            duration_ms = int((time.monotonic() - start_time) * 1000)

            Logger.event(
                "mission_compactor_success",
                goal=goal,
                nb_missions=len(similar_missions),
                advice=compacted.advice,
                is_novel=compacted.is_novel,
                confidence=compacted.confidence,
                duration_ms=duration_ms
            )

            if not compacted.advice or not compacted.advice.strip():
                compacted.advice = "Aucun conseil pertinent extrait. Utilisez une approche prudente."

            # --- 3. Stocker le résultat dans le cache ---
            if query_signatures and self.cache_manager:
                markers = []
                for s in query_signatures:
                    markers.append(f"{s.action.strip().lower()}|{s.object.strip().lower()}")
                await self.cache_manager.set(
                    "compactor",
                    cache_params,
                    compacted.model_dump(),
                    invalidation_markers=markers
                )
                Logger.debug(f"[MissionCompactor] Résultat stocké dans le cache pour {len(query_signatures)} signatures.")

            return compacted

        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            Logger.error(f"[MissionCompactor] Échec de la compaction : {e}")
            Logger.event(
                "mission_compactor_fallback",
                goal=goal,
                nb_missions=len(similar_missions),
                reason=str(e),
                duration_ms=duration_ms
            )
            return CompactedAdvice(
                advice="Impossible de synthétiser les missions similaires en raison d'une erreur technique. Soyez prudent.",
                is_novel=True,
                confidence=0.3
            )

    def _format_missions(self, missions: List[Dict[str, Any]]) -> str:
        """Formate la liste des missions pour le prompt."""
        lines = []
        for idx, m in enumerate(missions, 1):
            goal = m.get("goal", "Objectif inconnu")
            summary = m.get("summary", "Résumé non disponible")
            score = m.get("score", 0.0)
            lines.append(f"{idx}. **Mission** : {goal}")
            lines.append(f"   **Résumé** : {summary}")
            lines.append(f"   **Score de similarité** : {score:.3f}")
        return "\n".join(lines)

    def _fetch_lessons_for_missions(self, mission_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Récupère toutes les leçons brutes (avoid/prefer) associées à au moins une des missions.
        Utilise la colonne source_episodes_json qui contient la liste des mission_id.
        """
        if not mission_ids:
            return []
        try:
            # On va chercher toutes les leçons actives et filtrer manuellement
            # (car SQLite ne supporte pas bien JSON_ARRAY_OVERLAPS dans toutes les versions)
            all_lessons = self.lesson_store.get_active_lessons(
                entity_types=["Planner", "Executor", "Solver", "Presentator"],
                environment="simulated"  # on prend toutes, peu importe l'environnement
            )
            # Filtre : garder celles qui ont au moins un mission_id commun
            relevant = []
            for lesson in all_lessons:
                sources = lesson.get("source_episodes", [])
                if any(mid in sources for mid in mission_ids):
                    relevant.append(lesson)
            return relevant
        except Exception as e:
            Logger.error(f"[MissionCompactor] Erreur lors de la récupération des leçons : {e}")
            return []

    def _format_lessons(self, lessons: List[Dict[str, Any]]) -> str:
        """Formate les leçons pour le prompt (polarité et recommandation)."""
        if not lessons:
            return ""
        lines = []
        lines.append("Voici des leçons (issues d'échecs ou de succès après échec) extraites des missions similaires :")
        for idx, lesson in enumerate(lessons, 1):
            polarity = lesson.get("polarity", "avoid")
            recommendation = lesson.get("recommendation", "Pas de recommandation.")
            entity = lesson.get("entity_type", "Inconnue")
            conf = lesson.get("confidence", 0.5)
            lines.append(f"{idx}. **[{polarity.upper()}]** ({entity}, conf={conf:.2f}) : {recommendation}")
        return "\n".join(lines)
"""
core/entity_learner.py
======================
EntityLearner – Consolidation des leçons brutes (Phase L.2)
"""

from typing import Optional, List, Dict, Any
from utils.logger import Logger
from memory.lesson_store import LessonStore
from core.constants import ENTITY_LEARNER_MIN_EVIDENCE


class EntityLearner:
    def __init__(self, lesson_store: LessonStore, cache_manager=None):
        self.lesson_store = lesson_store
        self.cache_manager = cache_manager

    async def consolidate_if_needed(self) -> int:
        """
        Vérifie s'il y a des groupes de leçons brutes à consolider.
        Si oui, exécute la consolidation.
        Retourne le nombre de groupes consolidés.
        """
        groups = self.lesson_store.get_unconsolidated_groups()
        if not groups:
            Logger.debug("[EntityLearner] Aucun groupe à consolider.")
            return 0

        consolidated_count = 0
        for group in groups:
            try:
                await self._consolidate_group(
                    entity_type=group["entity_type"],
                    scope=group["scope"],
                    environment=group["environment"]
                )
                consolidated_count += 1
            except Exception as e:
                Logger.error(f"[EntityLearner] Échec consolidation groupe {group['scope']}: {e}")
        return consolidated_count

    async def _consolidate_group(self, entity_type: str, scope: str, environment: str) -> None:
        """
        Consolide un groupe spécifique.
        - Récupère toutes les brutes du groupe.
        - Sépare avoid / prefer.
        - Détermine la leçon gagnante (celle avec la meilleure confiance).
        - Crée une nouvelle leçon consolidée à partir de la gagnante.
        - (Optionnel) enregistre la résolution de conflit.
        """
        brutes = self.lesson_store.get_brute_lessons_by_group(entity_type, scope, environment)
        if len(brutes) < ENTITY_LEARNER_MIN_EVIDENCE:
            Logger.debug(f"[EntityLearner] Groupe {scope}: moins de {ENTITY_LEARNER_MIN_EVIDENCE} brutes, skip.")
            return

        # Séparer par polarité
        avoids = [b for b in brutes if b["polarity"] == "avoid"]
        prefers = [b for b in brutes if b["polarity"] == "prefer"]

        # Déterminer la leçon gagnante
        # Critère : confiance la plus élevée (puis evidence_count)
        def best_lesson(lessons):
            if not lessons:
                return None
            return max(lessons, key=lambda l: (l["confidence"], l["evidence_count"]))

        winner = None
        conflict_resolution = None

        if avoids and not prefers:
            winner = best_lesson(avoids)
            conflict_resolution = "Groupe monovalent (avoid uniquement)."
        elif prefers and not avoids:
            winner = best_lesson(prefers)
            conflict_resolution = "Groupe monovalent (prefer uniquement)."
        elif avoids and prefers:
            # Conflit : comparer les meilleurs
            best_avoid = best_lesson(avoids)
            best_prefer = best_lesson(prefers)
            # On garde celui avec la meilleure confiance globale
            if best_prefer["confidence"] >= best_avoid["confidence"]:
                winner = best_prefer
                conflict_resolution = (
                    f"Conflit résolu : prefer l'emporte (conf={best_prefer['confidence']:.2f} vs "
                    f"avoid={best_avoid['confidence']:.2f})."
                )
            else:
                winner = best_avoid
                conflict_resolution = (
                    f"Conflit résolu : avoid l'emporte (conf={best_avoid['confidence']:.2f} vs "
                    f"prefer={best_prefer['confidence']:.2f})."
                )

        if not winner:
            Logger.warning(f"[EntityLearner] Aucun gagnant pour {scope}")
            return

        # Fusionner les keywords et sources
        all_keywords = set()
        all_sources = set()
        for b in brutes:
            all_keywords.update(b.get("keywords", []))
            all_sources.update(b.get("source_episodes", []))

        # Somme des evidence_count et contradiction_count de toutes les brutes
        total_evidence = sum(b.get("evidence_count", 0) for b in brutes)
        total_contradiction = sum(b.get("contradiction_count", 0) for b in brutes)

        # Créer la leçon consolidée
        new_id = self.lesson_store.create_consolidated_lesson(
            entity_type=entity_type,
            scope=scope,
            environment=environment,
            from_lesson_id=winner["id"],
            recommendation=winner["recommendation"],
            confidence=winner["confidence"],
            evidence_count=total_evidence,
            contradiction_count=total_contradiction,
            keywords=list(all_keywords),
            source_episodes=list(all_sources),
            polarity=winner["polarity"],
            conflict_resolution=conflict_resolution
        )

        if new_id and self.cache_manager:
            # Invalider le cache Advisor pour ce scope
            try:
                await self.cache_manager.invalidate([scope])
                Logger.debug(f"[EntityLearner] Cache Advisor invalidé pour scope {scope}")
            except Exception as e:
                Logger.warning(f"[EntityLearner] Échec invalidation cache : {e}")

        Logger.info(f"[EntityLearner] Consolidation terminée pour {scope} (nouvel id={new_id})")
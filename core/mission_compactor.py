"""
core/mission_compactor.py
=========================
MissionCompactor – Synthèse stratégique des missions similaires.
V2 – Retour structuré (CompactedAdvice) avec jugement de nouveauté.
"""

import time
from typing import List, Dict, Any, Optional
from core.entity import Entity
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from utils.logger import Logger
from core.plan_models import CompactedAdvice  # ou import depuis plan_models


class MissionCompactor(Entity):
    """
    Entité dédiée à la compaction sémantique des missions similaires.
    Utilise le LLM pour synthétiser les patterns gagnants et les écueils,
    et pour juger de la nouveauté de la mission actuelle.
    """

    def __init__(self, name: str = "mission_compactor", parent: Optional[Entity] = None):
        super().__init__(name=name, role="compactor", llm=None, parent=parent)

    async def process(self, *args, **kwargs) -> CompactedAdvice:
        """
        Implémentation de la méthode abstraite de Entity.
        Redirige vers compact avec les paramètres appropriés.
        """
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
        runtime_state
    ) -> CompactedAdvice:
        """
        Synthétise la liste des missions similaires en un conseil stratégique
        et détermine si la mission actuelle est nouvelle.

        Returns:
            Un objet CompactedAdvice structuré.
        """
        if not similar_missions:
            Logger.debug("[MissionCompactor] Aucune mission similaire fournie, compaction ignorée.")
            # Si aucune mission similaire, on considère que c'est forcément nouveau
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
            missions_text = self._format_missions(similar_missions)

            loader = get_prompt_loader()
            prompt = loader.load(
                "mission_compactor.md",
                lang=runtime_state.language,
                goal=goal,
                missions=missions_text
            )

            # Utilisation de generate_structured avec le nouveau schéma
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

            # Sécurité : si l'advice est vide, on met un message par défaut
            if not compacted.advice or not compacted.advice.strip():
                compacted.advice = "Aucun conseil pertinent extrait. Utilisez une approche prudente."

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
            # Fallback : on renvoie un advice minimal, et on considère que c'est nouveau
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
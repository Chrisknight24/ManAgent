"""
core/markers/manager.py
=======================
Gestionnaire des marqueurs.
"""

from typing import List, Dict, Any
from utils.logger import Logger
from core.markers.base import BaseMarker
from core.markers.retry_marker import RetryMarker
from core.markers.failure_marker import FailureMarker
from core.markers.abstract_task_marker import AbstractTaskMarker
from core.markers.plan_rejected_marker import PlanRejectedMarker
from core.markers.novelty_marker import NoveltyMarker


class MarkerManager:
    def __init__(self):
        # Liste des marqueurs actifs. Modifiable facilement.
        self.markers: List[BaseMarker] = [
            RetryMarker(),
            FailureMarker(),
            AbstractTaskMarker(),
            PlanRejectedMarker(),
            NoveltyMarker(),
        ]

    def should_learn(self, context: Dict[str, Any]) -> bool:
        triggered = []
        for marker in self.markers:
            try:
                if marker.evaluate(context):
                    triggered.append(marker.name)
            except Exception as e:
                Logger.error(f"[MarkerManager] Erreur sur le marqueur {marker.name} : {e}")

        # Au moins 2 marqueurs activés pour déclencher l'apprentissage
        if len(triggered) >= 2:
            Logger.event(
                "learning_trigger",
                triggered_markers=triggered,
                mission_id=context.get("mission_id"),
                solver_id=context.get("solver_id"),
                session_id=context.get("session_id"),
            )
            Logger.info(f"[MarkerManager] Apprentissage déclenché par : {', '.join(triggered)}")
            return True

        Logger.debug("[MarkerManager] Aucun marqueur ou moins de 2 marqueurs activés, apprentissage ignoré.")
        return False

    def add_marker(self, marker: BaseMarker):
        """Ajoute un marqueur à la liste (pour les tests ou extensions)."""
        self.markers.append(marker)

    def remove_marker(self, name: str):
        """Retire un marqueur par son nom (pour les tests ou ajustements)."""
        self.markers = [m for m in self.markers if m.name != name]
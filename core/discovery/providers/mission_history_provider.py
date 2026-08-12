"""
core/discovery/providers/mission_history_provider.py
=====================================================
DataProvider pour l'historique des missions d'une session.
Expose les missions avec leurs mission_id et la cible spéciale "last_mission".
"""

from typing import List, Any, Dict, Optional
from core.discovery.data_provider import DataProvider
from core.i18n import _
from memory.mission_store import MissionStore
from utils.logger import Logger


class MissionHistoryProvider(DataProvider):
    """
    Fournit l'accès à l'historique des missions de la session courante.
    Les cibles sont :
    - "last_mission" : la mission la plus récente.
    - Un mission_id direct (ex: "abc123").
    """

    def __init__(self, session_id: str, mission_store: MissionStore):
        self.session_id = session_id
        self.mission_store = mission_store
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cached_targets: Optional[List[str]] = None

    def get_data_type(self) -> str:
        return "missions"

    def get_targets(self) -> List[str]:
        """
        Retourne la liste des cibles disponibles.
        - "last_mission" en premier (si au moins une mission existe).
        - Puis les mission_id de toutes les missions (du plus récent au plus ancien).
        """
        if self._cached_targets is None:
            episodes = self._get_episodes()
            targets = []
            if episodes:
                targets.append("last_mission")
            targets.extend([ep.get("mission_id") for ep in episodes if ep.get("mission_id")])
            self._cached_targets = targets
        return self._cached_targets

    def get_metadata(self, target: str) -> Dict[str, Any]:
        """
        Retourne les métadonnées d'une mission (goal, status, summary, etc.).
        """
        episode = self._resolve_target(target)
        if not episode:
            return {}

        return {
            "mission_id": episode.get("mission_id"),
            "goal": episode.get("goal", ""),
            "status": episode.get("status", ""),
            "summary": episode.get("summary", "Résumé non disponible"),
            "created_at": episode.get("created_at", ""),
            "finished_at": episode.get("finished_at", ""),
            "environment": episode.get("environment", ""),
            "refined_goal": episode.get("refined_goal", ""),
        }

    def get_data(self, target: str) -> Any:
        """
        Retourne l'épisode complet (arbre d'exécution, registre résolu, etc.).
        """
        return self._resolve_target(target)

    # =====================================================
    # MÉTHODES INTERNES
    # =====================================================

    def _get_episodes(self) -> List[Dict[str, Any]]:
        """Récupère les épisodes de la session, triés du plus récent au plus ancien."""
        if self._cache is None:
            episodes = self.mission_store.get_episodes_by_session(self.session_id)
            # Trier par date décroissante (les plus récentes en premier)
            episodes.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            self._cache = episodes
        return self._cache

    def _resolve_target(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Résout une cible en un épisode.
        La cible peut être :
        - "last_mission" : retourne la mission la plus récente (parsée).
        - un mission_id direct (parsé).
        """
        if not target:
            return None

        if target == "last_mission":
            episodes = self._get_episodes()
            if not episodes:
                return None
            # Récupère l'épisode parsé via get_episode
            return self.mission_store.get_episode(episodes[0].get("mission_id"))
        else:
            # mission_id direct
            return self.mission_store.get_episode(target)
"""
core/discovery/providers/mission_history_provider.py
=====================================================
DataProvider pour l'historique des missions d'une session.
Expose les missions avec des cibles par index (index:0, index:1, ...).
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
    - Un ID de mission direct (ex: "abc123")
    - Un index relatif (ex: "index:0" pour la dernière mission, "index:1" pour l'avant-dernière, etc.)
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
        Retourne la liste des cibles disponibles sous forme d'index.
        Ex: ["index:0", "index:1", "index:2", ...]
        """
        if self._cached_targets is None:
            episodes = self._get_episodes()
            self._cached_targets = [f"index:{i}" for i in range(len(episodes))]
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
            "environment": episode.get("environment", ""),
            "refined_goal": episode.get("refined_goal", ""),
            "index": self._get_index(target),
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
        - un mission_id direct
        - un index "index:0", "index:1", ...
        """
        if not target:
            return None

        episodes = self._get_episodes()
        if not episodes:
            return None

        # Cas : index relatif
        if target.startswith("index:"):
            try:
                idx = int(target.split(":", 1)[1])
                if 0 <= idx < len(episodes):
                    return episodes[idx]
                else:
                    Logger.warning(
                        f"[MissionHistoryProvider] Index {idx} hors limites (0..{len(episodes)-1}) pour la session {self.session_id}"
                    )
                    return None
            except ValueError:
                Logger.warning(f"[MissionHistoryProvider] Index invalide : {target}")
                return None

        # Cas : mission_id direct
        return self.mission_store.get_episode(target)

    def _get_index(self, target: str) -> Optional[int]:
        """Retourne l'index correspondant à une cible (si c'est un index)."""
        if target.startswith("index:"):
            try:
                return int(target.split(":", 1)[1])
            except ValueError:
                pass
        return None
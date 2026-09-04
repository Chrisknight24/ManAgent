"""
core/discovery/providers/mission_history_provider.py
=====================================================

DataProvider pour l'historique des missions d'une session.
Expose les missions avec leurs mission_id et la cible spéciale "last_mission".
"""

from typing import List, Any, Dict, Optional
import json
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset
from core.i18n import _
from memory.mission_store import MissionStore
from utils.logger import Logger

from pydantic import Field

class MissionDataAsset(DataAsset):
    """Asset représentant une mission dans l'historique."""
    data: Dict[str, Any] = Field(default_factory=dict)

    def dump_data(self) -> str:
        """
        Génère une représentation textuelle des données de la mission.
        Ici, on peut formater l'arbre d'exécution, etc.
        """
        # Pour faire simple, on dump en JSON.
        try:
            return json.dumps(self.data, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"[Erreur de sérialisation: {e}]"

from memory.mission_profile_store import MissionProfileStore

class MissionHistoryProvider(DataProvider):
    """
    Fournit l'accès à l'historique des missions de la session courante.
    Les cibles sont :
    - "last_mission" : la mission la plus récente.
    - Un mission_id direct (ex: "abc123").
    """

    def __init__(self, session_id: str, mission_store: MissionStore, profile_store: Optional[MissionProfileStore] = None):
        self.session_id = session_id
        self.mission_store = mission_store
        self.profile_store = profile_store
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cached_targets: Optional[List[str]] = None

    def get_data_type(self) -> str:
        return "missions"

    def get_targets(self) -> List[str]:
        if self._cached_targets is None:
            episodes = self._get_episodes()
            targets = []
            if episodes:
                targets.append("last_mission")
            targets.extend([ep.get("mission_id") for ep in episodes if ep.get("mission_id")])
            self._cached_targets = targets
        return self._cached_targets

    def get_asset(self, target: str) -> DataAsset:
        episode = self._resolve_target(target)
        if not episode:
            return MissionDataAsset(target_id=target, metadata={}, data={})
        
        mid = episode.get("mission_id", "")
        sigs = []
        if self.profile_store and mid:
            profs = self.profile_store.get_profiles_by_mission(mid)
            for p in profs:
                act = p.get("action", "")
                obj = p.get("object", "")
                if act and obj:
                    sigs.append(f"{act} {obj}")

        metadata = {
            "mission_id": mid,
            "goal": episode.get("goal", ""),
            "status": episode.get("status", ""),
            "summary": episode.get("summary", "Résumé non disponible"),
            "signatures": sigs,
            "created_at": episode.get("created_at", ""),
            "finished_at": episode.get("finished_at", ""),
            "environment": episode.get("environment", ""),
            "refined_goal": episode.get("refined_goal", ""),
        }
        return MissionDataAsset(target_id=target, metadata=metadata, data=episode)

    # =====================================================
    # MÉTHODES INTERNES
    # =====================================================

    def _get_episodes(self) -> List[Dict[str, Any]]:
        """Récupère les épisodes de la session, triés du plus récent au plus ancien."""
        if self._cache is None:
            episodes = self.mission_store.get_episodes_by_session(self.session_id)
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
            return self.mission_store.get_episode(episodes[0].get("mission_id"))
        else:
            return self.mission_store.get_episode(target)

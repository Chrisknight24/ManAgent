# memory/session_memory.py
# =====================================================
# PHASE 1 – SESSION MEMORY & MISSION CACHE
# Conteneur mémoire en RAM pour une session.
# Aucune logique cognitive, uniquement du stockage.
# =====================================================

from datetime import datetime
from typing import Optional, Dict, Any, List
from core.execution_models import ExecutionTree


class MissionCache:
    """
    Cache d'une mission unique.
    Stocke l'arbre d'exécution complet, les données résolues,
    et les métadonnées de la mission.
    """

    def __init__(self, mission_id: str, session_id: str, goal: str):
        self.mission_id = mission_id
        self.session_id = session_id
        self.goal = goal

        # Données techniques
        self.execution_tree: Optional[ExecutionTree] = None
        self.resolved_data: Dict[str, Any] = {}  # copie profonde du registre

        # Statut
        self.status: str = "pending"  # pending, running, success, failed, cancelled
        self.started_at: datetime = datetime.now()
        self.finished_at: Optional[datetime] = None

        # Télémétrie du Presentator
        self.presentator_result: Optional[Dict[str, Any]] = None

        # Résumé (sera rempli en Phase 5)
        self.summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Retourne un dictionnaire léger pour le logging / debug."""
        return {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "has_tree": self.execution_tree is not None,
            "presentator_result": self.presentator_result,
            "resolved_keys": list(self.resolved_data.keys()),
            "summary": self.summary,
        }


class SessionContext:
    """
    Contexte global de la session (l'âme de la session).
    Synthèse de ce qui s'est passé, du but global, des problèmes rencontrés.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id

        # --- NOUVEAU : empilement des objectifs raffinés ---
        # Chaque élément : {"text": "...", "timestamp": "...", "status": "..."}
        self.goal_stack: List[Dict[str, Any]] = []

        # Objectif global de l'utilisateur (peut évoluer) – gardé pour compatibilité
        self.global_goal: Optional[str] = None

        # Historique des missions (liste des mission_id)
        self.mission_history: List[str] = []

        # Dernier statut connu
        self.last_mission_status: Optional[str] = None

        # Problèmes récurrents (ex: "échec de keyboard")
        self.unresolved_issues: List[str] = []

        # --- NOUVEAU : mood de la session (inféré, utilisé uniquement par le Presentator) ---
        self.mood: Optional[str] = None

        # Résumé global de la session (vide pour l'instant)
        self.summary: str = ""

        # Dernière activité
        self.last_activity: datetime = datetime.now()
        self.recurrent_themes: List[Dict[str, Any]] = []   # <-- NOUVEAU

        self.discovery_history: List[str] = []  # <-- NOUVEAU
         # --- NOUVEAU : insights issus de la Progressive Disclosure ---
        self.discovery_insights: List[Dict[str, Any]] = []

    def touch(self):
        """Met à jour le timestamp de la dernière activité."""
        self.last_activity = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal_stack": self.goal_stack,
            "global_goal": self.global_goal,
            "mission_count": len(self.mission_history),
            "last_mission_status": self.last_mission_status,
            "unresolved_issues": self.unresolved_issues,
            "mood": self.mood,
            "summary": self.summary,
            "last_activity": self.last_activity.isoformat(),
            "discovery_history": self.discovery_history,
            "discovery_insights": self.discovery_insights,
        }


class SessionMemory:
    """
    Conteneur mémoire complet pour une session.
    Regroupe le contexte global et tous les caches de missions.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.context = SessionContext(session_id)
        self.mission_caches: Dict[str, MissionCache] = {}
        self.active_mission_id: Optional[str] = None

    def get_active_mission(self) -> Optional[MissionCache]:
        """Retourne la mission active, si elle existe."""
        if self.active_mission_id:
            return self.mission_caches.get(self.active_mission_id)
        return None

    def add_mission(self, mission_cache: MissionCache) -> None:
        """Ajoute un cache de mission et le définit comme actif."""
        self.mission_caches[mission_cache.mission_id] = mission_cache
        self.active_mission_id = mission_cache.mission_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "context": self.context.to_dict(),
            "active_mission_id": self.active_mission_id,
            "missions": [cache.to_dict() for cache in self.mission_caches.values()],
        }
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

        # Profil canonique (Skill Engine)
        self.profile_id: Optional[int] = None

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
            "profile_id": self.profile_id,
        }



class SessionContext:
    """
    Contexte global de la session (l'âme de la session).
    Synthèse de ce qui s'est passé, du but global, des problèmes rencontrés.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.goal_stack: List[Dict[str, Any]] = []
        self.global_goal: Optional[str] = None
        self.mission_history: List[str] = []
        self.last_mission_status: Optional[str] = None
        self.unresolved_issues: List[str] = []
        self.mood: Optional[str] = None
        self.summary: str = ""
        self.last_activity: datetime = datetime.now()
        self.recurrent_themes: List[Dict[str, Any]] = []

        # --- DISCOVERY FRAMEWORK ---
        self.discovery_history: List[str] = []
        self.discovery_insights: List[Dict[str, Any]] = []  # plat (compatibilité)
        self.insights_by_mission: Dict[str, List[Dict[str, Any]]] = {}

        # --- NOUVEAU : gestion multi‑cibles ---
        self.active_investigation_targets: List[str] = []  # toutes les cibles de la dernière PD
        # Note : on supprime active_investigation_mission_id

    def touch(self):
        self.last_activity = datetime.now()

    # Champs qui ont vocation à survivre à un rechargement de session
    # (depuis un store persistant type SQLite). Source unique de vérité :
    # tout store de persistance doit s'appuyer sur cette liste plutôt que de
    # dupliquer la connaissance de "quels champs existent" ailleurs (c'est
    # cette duplication qui avait fait dériver SessionStore et SessionContext
    # l'un de l'autre — `active_investigation_targets`/`insights_by_mission`
    # avaient été ajoutés ici mais jamais côté SQLite).
    PERSISTABLE_FIELDS = (
        "goal_stack",
        "unresolved_issues",
        "mission_history",
        "mood",
        "last_mission_status",
        "discovery_history",
        "insights_by_mission",
        "active_investigation_targets",
    )

    def to_persistable_dict(self) -> Dict[str, Any]:
        """Sous-ensemble de l'état à transmettre à un store persistant."""
        return {name: getattr(self, name) for name in self.PERSISTABLE_FIELDS}

    def restore_from_dict(self, data: Dict[str, Any]) -> None:
        """
        Restaure l'état depuis un dict rechargé (ex: SQLite via SessionStore).

        IMPORTANT : seules les clés effectivement PRÉSENTES dans `data` sont
        appliquées. Une clé absente (store plus ancien qui ne la supporte pas
        encore, migration en cours, etc.) laisse la valeur déjà en mémoire
        intacte au lieu de l'écraser par un défaut vide — c'est précisément
        l'inverse de l'ancien code qui faisait
        `session_memory.context.active_investigation_targets = session_data.get("active_investigation_targets", [])`
        et remettait donc `[]` à CHAQUE tour, y compris juste après qu'une
        Progressive Disclosure venait de le peupler.
        """
        for name in self.PERSISTABLE_FIELDS:
            if name in data:
                setattr(self, name, data[name])

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
            "insights_by_mission": self.insights_by_mission,
            "active_investigation_targets": self.active_investigation_targets,
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
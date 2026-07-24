"""
core/markers/base.py
====================
Classe abstraite pour les marqueurs de déclenchement d'apprentissage.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseMarker(ABC):
    """Un marqueur détermine si une mission doit déclencher l'apprentissage."""

    def __init__(self, name: str, weight: int = 1):
        self.name = name
        self.weight = weight

    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> bool:
        """
        Évalue le contexte de la mission et retourne True si le marqueur est activé.
        Le contexte contient :
            - goal: str
            - status: str (success, failed, cancelled)
            - execution_attempt: int
            - has_abstract_task: bool
            - plan_rejected: bool
            - is_novel: bool (du MissionCompactor)
            - mission_id: str
            - solver_id: str
            - session_id: str
        """
        pass

    def __repr__(self) -> str:
        return f"<Marker:{self.name} weight={self.weight}>"
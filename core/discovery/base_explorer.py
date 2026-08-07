"""
core/discovery/base_explorer.py
===============================
Interface abstraite pour tous les Explorers.
Un Explorer sait interpréter une demande de découverte (goal + target)
et la transformer en un plan d'action technique (DiscoveryPlan).
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from core.discovery.models import DiscoveryPlan
from core.runtime_state import RuntimeState
from core.discovery.data_provider import DataProvider
from core.llm import Llm  # <-- AJOUT pour le typage


class BaseExplorer(ABC):
    """
    Classe de base pour tous les Explorers.
    Chaque type de données (registry, file, image, etc.) doit implémenter
    cette interface.
    """

    def __init__(self, runtime_state: RuntimeState):
        self.runtime_state = runtime_state
        self._name = self.__class__.__name__

    @abstractmethod
    def get_data_type(self) -> str:
        """
        Retourne le type de données géré par cet Explorer.
        Ex: 'registry', 'file', 'image', 'mission', etc.
        """
        pass

    @abstractmethod
    def get_available_goals(self) -> List[str]:
        """
        Retourne la liste des goals techniques que cet Explorer peut traiter.
        Ces goals sont utilisés en interne par l'Explorer pour construire le plan.
        Ex: ['list_keys', 'describe_type', 'check_value', 'summarize']
        """
        pass

    @abstractmethod
    def get_tools_description(self) -> List[Dict[str, Any]]:
        """
        Retourne la description des outils disponibles pour cet Explorer.
        Format : [
            {
                "name": "describe_value",
                "description": "Retourne la description d'une variable",
                "parameters": {"type": "object", "properties": {...}}
            }
        ]
        """
        pass

    @abstractmethod
    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute un outil de l'Explorer.
        Retourne un dict avec 'success' (bool) et 'data' (Any).
        """
        pass

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        """
        Valide que la cible est accessible.
        Si un provider est passé, l'utilise pour valider.
        Par défaut, retourne True (à surcharger).
        """
        if provider:
            return target in provider.get_targets()
        return True

    @abstractmethod
    def create_signature(self, goal: str, target: str) -> str:
        """
        Crée une signature normalisée pour le cache.
        Format : "{data_type}://{target}/{goal}"
        """
        pass

    @abstractmethod
    async def generate_plan(
        self,
        goal: str,
        technical_goal: str,
        target: str,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None  # <-- NOUVEAU PARAMÈTRE
    ) -> DiscoveryPlan:
        """
        Génère un DiscoveryPlan à partir d'un goal en langage naturel,
        d'un technical_goal choisi par le LLM de l'entité, et d'une target.

        - llm : LLM à utiliser pour la génération du plan.
        - data_provider : fournisseur de données pour la validation et l'accès.
        - data_context : contexte de données générique (ex: un registre, un objet, etc.)
                         qui peut être utilisé par l'Explorer sans fixette.
        """
        pass

    def supports(self, data_type: str) -> bool:
        """Vérifie si cet Explorer supporte le type de données donné."""
        return self.get_data_type() == data_type
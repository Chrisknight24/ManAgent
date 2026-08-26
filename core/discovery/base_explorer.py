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
        self._current_data_context: Optional[Any] = None

    def set_data_context(self, data_context: Any) -> None:
        """Définit le contexte de données courant pour les outils de l'Explorer."""
        self._current_data_context = data_context
        
    def allow_successive_calls(self) -> bool:
        """
        Indique si cet Explorer peut être appelé plusieurs fois successivement 
        dans une même session de Progressive Disclosure.
        Par défaut, False pour éviter les boucles (l'outil sera masqué après son premier appel).
        À surcharger pour True sur les outils de pagination ou d'analyse complexe.
        """
        return False

    def get_scope_description(self) -> str:
        """
        Retourne la mission régalienne et le périmètre métier de cet Explorer pour le LLM.
        """
        return ""

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
    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        """
        Crée une signature normalisée pour le cache à partir des listes de cibles et de goals.
        Format :
        - Une seule cible : "{data_type}://{target}/{technical_goal}"
        - Plusieurs cibles : "{data_type}://multi/{targets_joined}/{goals_joined}"
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

    def get_non_cacheable_goals(self) -> List[str]:
        """
        Sous-ensemble de get_available_goals() dont le RÉSULTAT dépend du
        texte libre de la question (goal), pas seulement de (data_type,
        target, technical_goal) — typiquement les goals qui délèguent à un
        outil d'analyse LLM (ex: analyze_registry, analyze_execution_tree).
        La signature de cache ne contenant pas ce texte libre, mettre ces
        goals en cache ferait servir la réponse à une AUTRE question posée
        précédemment sur la même cible. Par défaut, aucun (tous cacheables) :
        à surcharger dans les Explorers concernés.
        """
        return []

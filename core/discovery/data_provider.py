"""
core/discovery/data_provider.py
===============================
Définit l'interface DataProvider pour la Progressive Disclosure.
Un DataProvider expose un type de données, ses cibles, et permet d'accéder
aux données brutes et métadonnées de manière dynamique (sans copie).
"""

from abc import ABC, abstractmethod
from typing import List, Any, Dict


class DataProvider(ABC):
    """
    Interface pour fournir des données à la Progressive Disclosure.
    Chaque entité peut enregistrer un ou plusieurs DataProviders.
    """

    @abstractmethod
    def get_data_type(self) -> str:
        """
        Retourne le type logique de la donnée (ex: 'registry', 'execution_tree', 'tools_view').
        Ce type doit correspondre à un Explorer enregistré dans le DiscoveryEngine.
        """
        pass

    @abstractmethod
    def get_targets(self) -> List[str]:
        """
        Retourne la liste des cibles disponibles pour ce type de données.
        Ex: pour 'registry' → ['img_data', 'notepad_status', ...]
        Ex: pour 'execution_tree' → ['mission_18', 'mission_42', ...]
        """
        pass

    @abstractmethod
    def get_data(self, target: str) -> Any:
        """
        Retourne la donnée brute pour une cible donnée.
        Ex: pour 'registry' et 'img_data' → le contenu de la variable.
        """
        pass

    @abstractmethod
    def get_metadata(self, target: str) -> Dict[str, Any]:
        """
        Retourne les métadonnées de la cible (description, source, taille, etc.).
        Utilisé pour enrichir le prompt sans exposer les données brutes.
        """
        pass
"""
core/discovery/data_provider.py
===============================

Définit l'interface DataProvider pour la Progressive Disclosure.
Un DataProvider expose un type de données, ses cibles, et permet d'accéder
aux données sous forme de DataAsset.
"""

from abc import ABC, abstractmethod
from typing import List, Any, Dict
from .data_asset import DataAsset

class DataProvider(ABC):
    """
    Interface pour fournir des données à la Progressive Disclosure.
    Chaque entité peut enregistrer un ou plusieurs DataProviders.
    """

    @abstractmethod
    def get_data_type(self) -> str:
        pass

    @abstractmethod
    def get_targets(self) -> List[str]:
        pass

    @abstractmethod
    def get_asset(self, target: str) -> DataAsset:
        pass

    # Rétrocompatibilité pour les Explorers existants
    def get_data(self, target: str) -> Any:
        asset = self.get_asset(target)
        if hasattr(asset, 'data'):
            return asset.data
        if hasattr(asset, 'value'):
            return asset.value
        return None

    def get_metadata(self, target: str) -> Dict[str, Any]:
        asset = self.get_asset(target)
        return asset.metadata

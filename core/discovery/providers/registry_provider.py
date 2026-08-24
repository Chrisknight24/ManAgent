"""
core/discovery/providers/registry_provider.py
=============================================

DataProvider pour le registre des variables d'un Solver.
"""

from typing import List, Any, Dict
import json
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset
from core.i18n import _

class RegistryDataAsset(DataAsset):
    """Asset représentant une variable du registre."""
    value: Any

    def dump_data(self) -> str:
        """
        Génère une représentation textuelle de la valeur de la variable.
        """
        # Troncature intelligente ou formatage
        if isinstance(self.value, str):
            # Troncature aveugle améliorée pour éviter les débordements
            if len(self.value) > 50000:
                return self.value[:50000] + "\n\n...[TRONQUÉ]..."
            return self.value
        try:
            val_str = json.dumps(self.value, indent=2, ensure_ascii=False)
            if len(val_str) > 50000:
                return val_str[:50000] + "\n\n...[TRONQUÉ]..."
            return val_str
        except Exception:
            return str(self.value)

class SolverRegistryProvider(DataProvider):
    """
    Fournit l'accès au registre de variables d'un Solver spécifique.
    Le registre est consulté en temps réel (pas de copie).
    """
    def __init__(self, solver):
        self.solver = solver

    def get_data_type(self) -> str:
        return "registry"

    def get_targets(self) -> List[str]:
        registry = getattr(self.solver, "variable_registry", {})
        return list(registry.keys()) if registry else []

    def get_asset(self, target: str) -> DataAsset:
        registry = getattr(self.solver, "variable_registry", {})
        info = registry.get(target, {})
        value = info.get("value")

        metadata = {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A"),
            "type": self._get_type_string(value)
        }
        return RegistryDataAsset(target_id=target, metadata=metadata, value=value)

    def _get_type_string(self, value) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        return "unknown"

"""
core/discovery/providers/registry_provider.py
=============================================
DataProvider pour le registre des variables d'un Solver.
"""

from typing import List, Any, Dict
from core.discovery.data_provider import DataProvider
from core.i18n import _


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
        """Retourne la liste des clés du registre."""
        registry = getattr(self.solver, "variable_registry", {})
        return list(registry.keys()) if registry else []

    def get_data(self, target: str) -> Any:
        """Retourne la valeur brute d'une variable."""
        registry = getattr(self.solver, "variable_registry", {})
        return registry.get(target, {}).get("value")

    def get_metadata(self, target: str) -> Dict[str, Any]:
        """Retourne les métadonnées d'une variable."""
        registry = getattr(self.solver, "variable_registry", {})
        info = registry.get(target, {})
        return {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A"),
            "type": self._get_type_string(info.get("value"))
        }

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
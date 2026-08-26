"""
core/discovery/providers/registry_provider.py
=============================================

DataProvider pour le registre des variables d'un Solver.
"""

from typing import List, Any, Dict, Optional
import json
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset, AssetMetadata
from core.i18n import _

class RegistryDataAsset(DataAsset):
    """Asset représentant une variable du registre."""
    value: Any = None

    def dump_data(self) -> str:
        """
        Génère une représentation textuelle de la valeur de la variable.
        """
        if isinstance(self.value, DataAsset):
            return self.value.dump_data()
            
        if isinstance(self.value, str):
            return self.value
        try:
            return json.dumps(self.value, indent=2, ensure_ascii=False)
        except Exception:
            return str(self.value)

    @classmethod
    def create(
        cls,
        target_id: str,
        value: Any,
        description: str = "",
        source: str = "solver",
        timestamp: str = "N/A"
    ) -> "RegistryDataAsset":
        uri = f"registry://{target_id}"
        
        # Obtenir le dump textuel pour calculer les métadonnées O(1)
        if isinstance(value, DataAsset):
            raw_text = value.dump_data()
        elif isinstance(value, str):
            raw_text = value
        else:
            try:
                raw_text = json.dumps(value, indent=2, ensure_ascii=False)
            except Exception:
                raw_text = str(value)

        char_count = len(raw_text)
        size_bytes = len(raw_text.encode("utf-8", errors="ignore"))
        line_count = len(raw_text.splitlines())
        token_estimate = max(1, char_count // 4)
        sha256_hash = DataAsset.compute_sha256(raw_text)

        meta = AssetMetadata(
            uri=uri,
            data_type="registry",
            name=f"Variable $@_{target_id}",
            size_bytes=size_bytes,
            char_count=char_count,
            line_count=line_count,
            token_estimate=token_estimate,
            mime_type="application/json" if isinstance(value, (dict, list)) else "text/plain",
            encoding="utf-8",
            sha256_hash=sha256_hash,
            capabilities=["read_slice", "get_head", "get_tail", "search"],
            custom_attributes={
                "variable_name": target_id,
                "source": source,
                "timestamp": timestamp,
                "description": description
            }
        )

        legacy_metadata = {
            "description": description or _("Pas de description"),
            "source": source,
            "timestamp": timestamp,
            "type": "asset" if isinstance(value, DataAsset) else cls._calc_type_string(value),
            "uri": uri
        }

        return cls(
            target_id=target_id,
            metadata=legacy_metadata,
            asset_meta=meta,
            value=value
        )

    @staticmethod
    def _calc_type_string(value: Any) -> str:
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
        
        # Si un DataAsset complet est déjà stocké
        if "asset" in info and isinstance(info["asset"], DataAsset):
            return info["asset"]
            
        value = info.get("value")
        if isinstance(value, DataAsset):
            return value

        return RegistryDataAsset.create(
            target_id=target,
            value=value,
            description=info.get("description", _("Pas de description")),
            source=info.get("source", _("Inconnu")),
            timestamp=info.get("timestamp", "N/A")
        )


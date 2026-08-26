from typing import List, Dict, Any, Optional
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset
from core.discovery.asset_registry import AssetRegistry
from core.discovery.input_ingestor import FileDataAsset, UserPayloadDataAsset


class FilesProvider(DataProvider):
    """
    DataProvider pour les fichiers (pièces jointes, documents, logs) et entrées volumineuses.
    Se branche directement sur l'AssetRegistry de session.
    data_type: 'files' ou 'inputs'
    """
    def __init__(self, registry: AssetRegistry, data_type: str = "files"):
        self.registry = registry
        self._data_type = data_type

    def get_data_type(self) -> str:
        return self._data_type

    def get_targets(self) -> List[str]:
        assets = self.registry.list_assets(scheme_filter=self._data_type)
        targets = [a.target_id for a in assets]
        return targets if targets else ["default"]

    def get_asset(self, target: str) -> DataAsset:
        asset = self.registry.resolve_asset(f"{self._data_type}://{target}") or self.registry.resolve_asset(target)
        if asset:
            return asset
        
        # Fallback si l'asset demandé n'existe pas encore
        return FileDataAsset(
            target_id=target,
            filename=target,
            raw_content=f"[Fichier ou ressource '{target}' introuvable dans le registre de session]",
            metadata={"status": "not_found", "target": target}
        )

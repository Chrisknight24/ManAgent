from typing import Dict, Any, List, Optional
import os
import hashlib
from .data_asset import DataAsset, AssetMetadata


class AssetRegistry:
    """
    Registre d'Assets de session.
    Centralise l'indexation, le stockage et la résolution par URI de tous les DataAssets.
    """
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._assets: Dict[str, DataAsset] = {}  # index par URI standardisée
        self._target_index: Dict[str, str] = {}  # mapping target_id -> URI

    @staticmethod
    def build_uri(scheme: str, path: str) -> str:
        """Normalise et construit une URI standardisée."""
        clean_scheme = scheme.strip().lower().rstrip(":/")
        clean_path = path.strip().lstrip("/")
        return f"{clean_scheme}://{clean_path}"

    def register_asset(self, asset: DataAsset, scheme: Optional[str] = None) -> str:
        """
        Enregistre un asset dans le registre de session.
        Garantit que l'asset possède son AssetMetadata et une URI valide.
        """
        target = asset.target_id
        inferred_scheme = scheme or "asset"
        
        if asset.asset_meta and asset.asset_meta.uri:
            uri = asset.asset_meta.uri
        elif "uri" in asset.metadata:
            uri = str(asset.metadata["uri"])
        else:
            uri = self.build_uri(inferred_scheme, target)

        if not asset.asset_meta:
            content_sample = asset.dump_data()
            lines = content_sample.splitlines() if content_sample else []
            char_count = len(content_sample)
            size_bytes = len(content_sample.encode("utf-8", errors="ignore"))
            # Estimation heuristique : 1 token ~= 4 caractères
            token_estimate = max(1, char_count // 4)
            sha256 = DataAsset.compute_sha256(content_sample)
            
            import mimetypes
            guessed_mime, _ = mimetypes.guess_type(target)
            mime_type = guessed_mime or "text/plain"
            
            asset.asset_meta = AssetMetadata(
                uri=uri,
                data_type=inferred_scheme,
                name=target,
                size_bytes=size_bytes,
                char_count=char_count,
                line_count=len(lines),
                token_estimate=token_estimate,
                mime_type=mime_type,
                sha256_hash=sha256,
                capabilities=asset.get_capabilities(),
                custom_attributes=asset.metadata
            )
        else:
            asset.asset_meta.uri = uri

        self._assets[uri] = asset
        self._target_index[target] = uri
        return uri

    def get_asset_by_uri(self, uri: str) -> Optional[DataAsset]:
        """Résout un asset via son URI exacte."""
        return self._assets.get(uri)

    def get_asset_by_target(self, target: str) -> Optional[DataAsset]:
        """Résout un asset par son target_id local."""
        if target in self._target_index:
            return self._assets.get(self._target_index[target])
        # Recherche directe dans les clés d'URI
        for uri, asset in self._assets.items():
            if asset.target_id == target:
                return asset
        return None

    def resolve_asset(self, identifier: str) -> Optional[DataAsset]:
        """Résolution flexible (URI ou Target)."""
        if "://" in identifier:
            return self.get_asset_by_uri(identifier)
        return self.get_asset_by_target(identifier)

    def list_assets(self, scheme_filter: Optional[str] = None) -> List[DataAsset]:
        """Liste tous les assets de la session (avec filtre optionnel)."""
        if not scheme_filter:
            return list(self._assets.values())
        clean_scheme = scheme_filter.strip().lower().rstrip(":/") + "://"
        return [a for uri, a in self._assets.items() if uri.startswith(clean_scheme)]

    def generate_manifest(self, max_assets: int = 15, preview_lines: int = 4) -> str:
        """
        Génère la section Manifeste compacte pour le prompt de l'Orchestrateur.
        """
        if not self._assets:
            return ""

        manifest_lines = ["### [RESSOURCES & DATA ASSETS DISPONIBLES DANS CETTE SESSION]"]
        
        for idx, (uri, asset) in enumerate(self._assets.items()):
            if idx >= max_assets:
                manifest_lines.append(f"- ... et {len(self._assets) - max_assets} autres assets disponibles.")
                break
                
            meta = asset.asset_meta
            name = meta.name if meta else asset.target_id
            dtype = meta.data_type if meta else "data"
            size_kb = round((meta.size_bytes if meta else 0) / 1024, 1)
            lines_cnt = meta.line_count if meta else len(asset.dump_data().splitlines())
            tokens_est = meta.token_estimate if meta else "?"
            caps = ", ".join(asset.get_capabilities())
            
            manifest_lines.append(
                f"- **URI**: `{uri}` (Nom: {name}, Type: {dtype})\n"
                f"  Taille: {size_kb} Ko | {lines_cnt} lignes | ~{tokens_est} tokens | Capacités: [{caps}]"
            )
            
            # Aperçu compact déterministe
            preview = asset.get_preview(max_lines=preview_lines, max_chars=400)
            if preview and preview != "[Contenu vide]":
                indented_preview = "\n".join(f"    {l}" for l in preview.splitlines())
                manifest_lines.append(f"  Aperçu:\n{indented_preview}")
                
        manifest_lines.append("*(Note : Le contenu complet des assets n'est pas injecté par défaut. Utilise le DiscoveryEngine pour forer).*")
        return "\n".join(manifest_lines)

    def to_dict(self) -> Dict[str, Any]:
        """
        Exporte tous les assets du registre sous forme de dictionnaire JSON-sérialisable.
        """
        serialized_assets = []
        for uri, asset in self._assets.items():
            try:
                asset_dict = asset.model_dump()
                asset_dict["_class_name"] = asset.__class__.__name__
                serialized_assets.append(asset_dict)
            except Exception as e:
                from utils.logger import Logger
                Logger.error(f"[AssetRegistry] Erreur sérialisation asset {uri}: {e}")
        return {
            "session_id": self.session_id,
            "assets": serialized_assets
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """
        Restaure les assets depuis un dictionnaire de session persisté.
        """
        if not data or not isinstance(data, dict) or "assets" not in data:
            return

        from core.discovery.data_asset import DataAsset, ToolOutputDataAsset, GenericDataAsset
        from core.discovery.input_ingestor import UserPayloadDataAsset, FileDataAsset
        from utils.logger import Logger

        class_map = {
            "ToolOutputDataAsset": ToolOutputDataAsset,
            "FileDataAsset": FileDataAsset,
            "UserPayloadDataAsset": UserPayloadDataAsset,
            "GenericDataAsset": GenericDataAsset,
            "DataAsset": GenericDataAsset,
        }

        assets_list = data.get("assets", [])
        restored_count = 0
        for item in assets_list:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            cls_name = item_copy.pop("_class_name", "GenericDataAsset")
            cls = class_map.get(cls_name, GenericDataAsset)
            try:
                asset = cls.model_validate(item_copy)
                uri = asset.get_uri()
                self._assets[uri] = asset
                self._target_index[asset.target_id] = uri
                restored_count += 1
            except Exception as e:
                Logger.error(f"[AssetRegistry] Échec restauration asset (type={cls_name}): {e}")

        if restored_count > 0:
            Logger.info(f"[AssetRegistry] {restored_count} DataAsset(s) restauré(s) pour la session {self.session_id}.")


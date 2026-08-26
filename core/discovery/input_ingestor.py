from typing import Dict, Any, List, Optional
import os
import mimetypes
from pydantic import BaseModel, Field

from core.discovery.data_asset import DataAsset, AssetMetadata
from core.discovery.asset_registry import AssetRegistry


class UserPayloadDataAsset(DataAsset):
    """
    Asset encapsulant un message utilisateur volumineux.
    URI format: inputs://turn_{turn_index}/payload
    """
    raw_content: str = Field(default="", description="Texte intégral et brut du message utilisateur")
    turn_index: int = Field(default=0, description="Numéro du tour conversationnel")

    def dump_data(self) -> str:
        return self.raw_content


class FileDataAsset(DataAsset):
    """
    Asset encapsulant un fichier (pièce jointe, document, code, log).
    URI format: files://{filename}
    """
    filename: str = Field(default="", description="Nom du fichier")
    filepath: Optional[str] = Field(default=None, description="Chemin physique éventuel sur disque")
    raw_content: Optional[str] = Field(default=None, description="Contenu texte si chargé en mémoire")

    def dump_data(self) -> str:
        if self.raw_content is not None:
            return self.raw_content
        if self.filepath and os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                return f"[Erreur lecture fichier {self.filename}: {e}]"
        return f"[Fichier {self.filename} introuvable]"


class IngestionPolicy(BaseModel):
    """
    Politique de seuils volumétriques configurables.
    Pas de constantes magiques codées en dur.
    """
    inline_limit_chars: int = Field(default=4000, description="En-dessous, le message reste direct/inline (~1000 tokens)")
    soft_asset_limit_chars: int = Field(default=25000, description="Asset créé, preview standard généré (~6000 tokens)")
    hard_asset_limit_chars: int = Field(default=100000, description="Très gros asset, forage et fenêtrage stricts exigés")


class IngestionResult(BaseModel):
    """Résultat de l'analyse d'ingestion d'une entrée utilisateur."""
    is_asset: bool = Field(..., description="True si transformé en DataAsset, False si reste inline")
    display_content: str = Field(..., description="Contenu à injecter dans le prompt actif (texte direct ou chapeau avec manifeste)")
    created_assets: List[str] = Field(default_factory=list, description="Liste des URI des assets créés")
    original_char_count: int = Field(default=0, description="Nombre de caractères du texte original")
    estimated_tokens: int = Field(default=0, description="Nombre de tokens estimés")


class InputIngestor:
    """
    Moteur d'ingestion, de détection volumétrique et d'instanciation déterministe de DataAssets.
    100% Déterministe en O(1) : Zéro appel LLM obligatoire à l'ingestion.
    """
    def __init__(self, policy: Optional[IngestionPolicy] = None):
        self.policy = policy or IngestionPolicy()

    def ingest(
        self,
        user_text: str,
        turn_index: int,
        session_id: str,
        registry: AssetRegistry,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> IngestionResult:
        """
        Ingère le tour utilisateur, évalue la volumétrie et enregistre les DataAssets nécessaires.
        """
        raw_text = user_text or ""
        char_len = len(raw_text)
        token_est = max(1, char_len // 4)
        created_uris = []

        # 1. Traitement des pièces jointes (attachments) si présentes
        if attachments:
            for att in attachments:
                att_name = att.get("name") or att.get("filename") or "unnamed_file.txt"
                att_content = att.get("content") or att.get("raw_content") or ""
                att_path = att.get("path") or att.get("filepath")
                
                # Déterminer les capacités en fonction de l'extension
                ext = os.path.splitext(att_name)[1].lower()
                caps = ["read_slice", "get_head", "get_tail", "search"]
                if ext in (".csv", ".json", ".parquet"):
                    caps.extend(["schema", "sample_records"])
                elif ext in (".py", ".ts", ".js", ".cpp", ".rs"):
                    caps.extend(["list_symbols", "extract_function"])
                elif ext == ".pdf":
                    caps.extend(["read_page", "table_of_contents"])

                file_asset = FileDataAsset(
                    target_id=att_name,
                    filename=att_name,
                    filepath=att_path,
                    raw_content=att_content if att_content else None,
                    metadata={"source": "user_attachment", "turn_index": turn_index, "capabilities": caps}
                )
                uri = registry.register_asset(file_asset, scheme="files")
                created_uris.append(uri)

        # 2. Évaluation de la volumétrie du texte utilisateur
        if char_len <= self.policy.inline_limit_chars and not attachments:
            # Cas A : INLINE STANDARD (court)
            return IngestionResult(
                is_asset=False,
                display_content=raw_text,
                created_assets=[],
                original_char_count=char_len,
                estimated_tokens=token_est
            )

        # Cas B : VOLUMINEUX -> Création d'un UserPayloadDataAsset
        if char_len > self.policy.inline_limit_chars:
            payload_asset = UserPayloadDataAsset(
                target_id=f"turn_{turn_index}",
                turn_index=turn_index,
                raw_content=raw_text,
                metadata={
                    "type": "user_payload",
                    "turn_index": turn_index,
                    "session_id": session_id,
                    "capabilities": ["read_slice", "get_head", "get_tail", "search"]
                }
            )
            uri = registry.register_asset(payload_asset, scheme="inputs")
            created_uris.append(uri)

            # Génération du chapeau exécutif déterministe (Head + Tail)
            preview_sample = payload_asset.get_preview(max_lines=10, max_chars=1200)
            lines_total = len(raw_text.splitlines())
            
            display_header = (
                f"[MESSAGE UTILISATEUR VOLUMINEUX DÉTECTÉ — {char_len} caractères, ~{token_est} tokens, {lines_total} lignes]\n"
                f"L'intégralité du texte a été sécurisée et enregistrée sous l'Asset : `{uri}`\n\n"
                f"Aperçu (début & fin du message) :\n"
                f"----------------------------------------\n"
                f"{preview_sample}\n"
                f"----------------------------------------\n"
                f"*(Consigne cognitive : Analyse l'intention générale. Si tu as besoin d'extraire des sections précises, utilise une DiscoveryRequest sur `{uri}`).*"
            )

            return IngestionResult(
                is_asset=True,
                display_content=display_header,
                created_assets=created_uris,
                original_char_count=char_len,
                estimated_tokens=token_est
            )

        # Cas C : Texte court mais avec des pièces jointes attachées
        return IngestionResult(
            is_asset=bool(created_uris),
            display_content=raw_text,
            created_assets=created_uris,
            original_char_count=char_len,
            estimated_tokens=token_est
        )

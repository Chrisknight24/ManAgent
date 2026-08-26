from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import hashlib
import re
from pydantic import BaseModel, Field, model_validator


class AssetMetadata(BaseModel):
    """
    Métadonnées techniques déterministes associées à un DataAsset.
    Calculées en O(1) sans appel LLM.
    """
    uri: str = Field(..., description="URI standardisée de l'asset (ex: files://logs.txt, inputs://turn_1/payload)")
    data_type: str = Field(default="generic", description="Type ou schéma de la donnée (ex: files, inputs, outputs, history, facts, registry)")
    name: str = Field(default="", description="Nom convivial de l'asset ou fichier")
    size_bytes: int = Field(default=0, description="Taille en octets")
    char_count: int = Field(default=0, description="Nombre de caractères")
    line_count: int = Field(default=0, description="Nombre de lignes si applicable")
    token_estimate: int = Field(default=0, description="Estimation du nombre de tokens")
    mime_type: str = Field(default="text/plain", description="Type MIME détecté")
    encoding: str = Field(default="utf-8", description="Encodage du contenu")
    sha256_hash: str = Field(default="", description="Empreinte SHA-256 du contenu brut")
    capabilities: List[str] = Field(
        default_factory=lambda: ["read_slice", "get_head", "get_tail", "search"],
        description="Capacités de forage exposées au DiscoveryEngine"
    )
    custom_attributes: Dict[str, Any] = Field(default_factory=dict, description="Attributs spécifiques supplémentaires")


class DataAsset(BaseModel, ABC):
    """
    Classe de base universelle pour toutes les ressources de données de MANAGENT.
    Un DataAsset représente une donnée persistée et adressable via une URI.
    """
    target_id: str = Field(..., description="Cible ou identifiant local")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Dictionnaire de métadonnées pour compatibilité historique")
    asset_meta: Optional[AssetMetadata] = Field(default=None, description="Métadonnées techniques enrichies")

    def get_uri(self) -> str:
        if self.asset_meta and self.asset_meta.uri:
            return self.asset_meta.uri
        if "uri" in self.metadata:
            return str(self.metadata["uri"])
        return f"asset://{self.target_id}"

    def get_capabilities(self) -> List[str]:
        if self.asset_meta and self.asset_meta.capabilities:
            return self.asset_meta.capabilities
        return self.metadata.get("capabilities", ["read_slice", "get_head", "get_tail", "search"])

    @abstractmethod
    def dump_data(self) -> str:
        """
        Génère une représentation textuelle des données liées à cet asset.
        """
        pass

    def get_preview(self, max_lines: int = 10, max_chars: int = 1500) -> str:
        """
        Génère un aperçu déterministe compact pour le Manifeste de l'Orchestrateur.
        Utilise un tronquage au milieu (Head + Tail) si le texte dépasse max_lines ou max_chars,
        afin de conserver le début (contexte/structure) ET la fin (consignes/instructions utilisateur).
        """
        full_text = self.dump_data()
        if not full_text:
            return "[Contenu vide]"
        
        lines = full_text.splitlines()
        total_lines = len(lines)
        
        if total_lines <= max_lines:
            preview_text = "\n".join(lines)
            if len(preview_text) > max_chars:
                half = (max_chars - 60) // 2
                preview_text = (
                    preview_text[:half] +
                    f"\n... [~{len(preview_text) - max_chars} caractères tronqués au milieu] ...\n" +
                    preview_text[-half:]
                )
            return preview_text

        # Tronquage intelligent par lignes : Head + Tail
        head_count = max(1, max_lines // 2)
        tail_count = max(1, max_lines - head_count)
        
        head_lines = lines[:head_count]
        tail_lines = lines[-tail_count:]
        skipped_lines = total_lines - (head_count + tail_count)
        
        preview_text = (
            "\n".join(head_lines) +
            f"\n... ({skipped_lines} lignes tronquées au milieu) ...\n" +
            "\n".join(tail_lines)
        )
        
        if len(preview_text) > max_chars:
            half = (max_chars - 60) // 2
            preview_text = (
                preview_text[:half] +
                "\n... [aperçu tronqué au milieu] ...\n" +
                preview_text[-half:]
            )
            
        return preview_text

    def get_head(self, n_lines: int = 20) -> List[str]:
        """Retourne les N premières lignes."""
        text = self.dump_data()
        return text.splitlines()[:n_lines]

    def get_tail(self, n_lines: int = 20) -> List[str]:
        """Retourne les N dernières lignes."""
        text = self.dump_data()
        return text.splitlines()[-n_lines:]

    def read_slice(self, from_line: int, to_line: int) -> List[str]:
        """
        Retourne une tranche de lignes (1-indexé).
        """
        lines = self.dump_data().splitlines()
        start = max(0, from_line - 1)
        end = min(len(lines), to_line)
        return lines[start:end]

    def search_lines(self, query: str, regex: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Recherche textuelle ou regex basique ligne par ligne, avec repli intelligent
        sur l'extraction de motifs (horodatages, mots-clés) si la requête initiale est verbeuse.
        """
        results = self._do_search(query, regex, limit)
        if results:
            return results

        # Repli : si 0 résultat et query verbeuse, tenter d'extraire un horodatage (ex: 02:11:21)
        time_match = re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', query)
        if time_match and time_match.group(0) != query:
            return self._do_search(time_match.group(0), regex=False, limit=limit)

        return results

    def _do_search(self, query: str, regex: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
        results = []
        lines = self.dump_data().splitlines()
        
        pattern = None
        if regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except Exception:
                pattern = None
        
        q_lower = query.lower()
        for idx, line in enumerate(lines, start=1):
            matched = False
            if pattern:
                matched = bool(pattern.search(line))
            else:
                matched = q_lower in line.lower()
                
            if matched:
                results.append({
                    "line_number": idx,
                    "content": line.strip()
                })
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def compute_sha256(content: str) -> str:
        """Calcule le hash SHA-256 d'une chaîne."""
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


class GenericDataAsset(DataAsset):
    """
    DataAsset générique de secours pour les données sans sous-classe spécifique.
    """
    raw_content: str = Field(default="", description="Contenu textuel brut")

    def dump_data(self) -> str:
        return self.raw_content


class ToolOutputDataAsset(DataAsset):
    """
    DataAsset représentant la sortie brute (ou volumineuse) d'un outil exécuté.
    URI format: outputs://step_{step_id}_{tool_name}
    Permet de conserver l'intégralité du retour (logs, JSON, stdout) sans encombrer
    les prompts LLM ni l'arbre d'exécution.
    """
    raw_output: str = Field(default="", description="Sortie textuelle ou dump brut de l'outil")

    @model_validator(mode='before')
    @classmethod
    def _coerce_input(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "raw_output" not in data:
                if "raw_data" in data:
                    data["raw_output"] = str(data["raw_data"])
                elif "data" in data:
                    data["raw_output"] = str(data["data"])
                else:
                    data["raw_output"] = ""
            elif not isinstance(data["raw_output"], str):
                data["raw_output"] = str(data["raw_output"])
        return data

    def dump_data(self) -> str:
        return self.raw_output

    @classmethod
    def create(
        cls,
        step_id: Any,
        tool_name: str,
        raw_output: str,
        session_id: str = "default",
        custom_attrs: Optional[Dict[str, Any]] = None
    ) -> "ToolOutputDataAsset":
        attrs = custom_attrs or {}
        clean_tool = re.sub(r'[^a-zA-Z0-9_\-]', '_', tool_name)
        mission_id = attrs.get("mission_id")
        mission_prefix = f"m_{str(mission_id)[:8]}_" if mission_id else ""
        target_id = f"{mission_prefix}step_{step_id}_{clean_tool}"
        uri = f"outputs://{target_id}"
        raw_output_str = str(raw_output)
        char_count = len(raw_output_str)
        size_bytes = len(raw_output_str.encode("utf-8", errors="ignore"))
        line_count = len(raw_output_str.splitlines())
        token_estimate = max(1, char_count // 4)
        sha256_hash = DataAsset.compute_sha256(raw_output_str)

        attrs = custom_attrs or {}
        attrs.update({
            "step_id": str(step_id),
            "tool_name": tool_name,
            "session_id": session_id
        })

        meta = AssetMetadata(
            uri=uri,
            data_type="outputs",
            name=f"Sortie outil {tool_name} (Étape {step_id})",
            size_bytes=size_bytes,
            char_count=char_count,
            line_count=line_count,
            token_estimate=token_estimate,
            mime_type="text/plain",
            encoding="utf-8",
            sha256_hash=sha256_hash,
            capabilities=["read_slice", "get_head", "get_tail", "search"],
            custom_attributes=attrs
        )

        return cls(
            target_id=target_id,
            metadata={"uri": uri, "size": size_bytes, "tool": tool_name, "step": step_id},
            asset_meta=meta,
            raw_output=raw_output_str
        )


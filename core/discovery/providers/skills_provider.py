"""
core/discovery/providers/skills_provider.py
===========================================

DataProvider pour les compétences (Skills) de ManAgent.
Permet la Progressive Disclosure sur les structures de compétences,
manifests, paramètres attendus et méta-plans sans saturer le contexte.
"""

from typing import List, Any, Dict, Optional
import json
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset, AssetMetadata
from core.skills.registry import SkillRegistry
from core.i18n import _

class SkillDataAsset(DataAsset):
    """Asset représentant une compétence (Skill)."""
    skill_data: Dict[str, Any] = {}

    def dump_data(self) -> str:
        """Génère une représentation JSON formatée de la compétence."""
        try:
            return json.dumps(self.skill_data, indent=2, ensure_ascii=False)
        except Exception:
            return str(self.skill_data)

    @classmethod
    def create(
        cls,
        skill_id: str,
        skill_data: Dict[str, Any],
        description: str = "",
        status: str = "PRODUCTION",
        version: int = 1
    ) -> "SkillDataAsset":
        uri = f"skill://{skill_id}"
        
        try:
            raw_text = json.dumps(skill_data, indent=2, ensure_ascii=False)
        except Exception:
            raw_text = str(skill_data)

        char_count = len(raw_text)
        size_bytes = len(raw_text.encode("utf-8", errors="ignore"))
        line_count = len(raw_text.splitlines())
        token_estimate = max(1, char_count // 4)
        sha256_hash = DataAsset.compute_sha256(raw_text)

        meta = AssetMetadata(
            uri=uri,
            data_type="skills",
            name=f"Skill {skill_id} (v{version})",
            size_bytes=size_bytes,
            char_count=char_count,
            line_count=line_count,
            token_estimate=token_estimate,
            mime_type="application/json",
            encoding="utf-8",
            sha256_hash=sha256_hash,
            capabilities=["read_slice", "get_head", "get_tail", "search"],
            custom_attributes={
                "skill_id": skill_id,
                "version": version,
                "status": status,
                "description": description
            }
        )

        legacy_metadata = {
            "description": description or _("Compétence automatisée"),
            "status": status,
            "version": version,
            "type": "skill",
            "uri": uri
        }

        return cls(
            target_id=skill_id,
            metadata=legacy_metadata,
            asset_meta=meta,
            skill_data=skill_data
        )


class SkillsProvider(DataProvider):
    """
    Fournit l'accès à la bibliothèque de compétences (SkillRegistry).
    Permet aux entités (Planner, Solver, Synthesizer, Repair) d'explorer
    les compétences enregistrées à la demande.
    """
    def __init__(self, registry: Optional[SkillRegistry] = None, production_only: bool = True):
        self.registry = registry or SkillRegistry()
        self.production_only = production_only

    def get_data_type(self) -> str:
        return "skills"

    def get_targets(self) -> List[str]:
        """Retourne la liste des identifiants de compétences disponibles (production uniquement par défaut)."""
        try:
            skills = self.registry.list_all_skills()
            if self.production_only:
                return [
                    s["id"] for s in skills 
                    if s.get("id") and s.get("production_version") is not None and s.get("state") == "PRODUCTION"
                ]
            return [s["id"] for s in skills if "id" in s]
        except Exception:
            return []

    def get_asset(self, target: str) -> DataAsset:
        """Récupère le manifeste et le méta-plan d'une compétence spécifique."""
        try:
            manifest, version = self.registry.get_active_skill(target)
            if not manifest or not version:
                fallback_doc = {"skill_id": target, "error": f"Aucune version active en production pour la compétence '{target}'."}
                return SkillDataAsset.create(
                    skill_id=target,
                    skill_data=fallback_doc,
                    description=f"Compétence indisponible en production : {target}",
                    status="UNAVAILABLE",
                    version=0
                )
            flow_payload = self.registry.get_flow_payload(target, version.version if version else None)
            
            skill_doc = {
                "skill_id": target,
                "name": manifest.name if manifest else target,
                "description": manifest.description if manifest else "",
                "status": version.state.value if version else "UNKNOWN",
                "version": version.version if version else 1,
                "parameters_schema": manifest.parameters_schema if manifest else {},
                "checkpoints": [cp.checkpoint_id for cp in manifest.checkpoints] if manifest and manifest.checkpoints else [],
                "meta_plan": flow_payload.get("meta_plan", []) if flow_payload else []
            }

            return SkillDataAsset.create(
                skill_id=target,
                skill_data=skill_doc,
                description=skill_doc["description"],
                status=skill_doc["status"],
                version=skill_doc["version"]
            )
        except Exception as e:
            fallback_doc = {"skill_id": target, "error": str(e)}
            return SkillDataAsset.create(
                skill_id=target,
                skill_data=fallback_doc,
                description=f"Erreur chargement skill {target}"
            )

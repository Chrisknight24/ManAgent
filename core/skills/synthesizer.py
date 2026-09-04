import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from core.llm import Llm
from core.skills.models import SkillManifest, ExecutionEnvironment, Checkpoint, SkillState
from core.skills.registry import SkillRegistry
from utils.logger import Logger
from core.prompt_loader import PromptLoader

class DynamicParameterDef(BaseModel):
    type: str = Field(..., description="Type of the parameter (e.g., string, number, boolean)")
    description: str = Field(..., description="Description of the parameter")

class SynthesizedPlanNode(BaseModel):
    step_id: str
    type: str
    action: Optional[str] = None
    description: str
    arguments: Optional[Dict[str, Any]] = None
    expected_result: Optional[str] = None
    output_var: Optional[str] = None
    execute_if: Optional[str] = None

class SkillSynthesisResult(BaseModel):
    description: str
    dynamic_parameters: Dict[str, DynamicParameterDef] = Field(default_factory=dict)
    meta_plan: List[SynthesizedPlanNode] = Field(default_factory=list)

class SkillSynthesizer:
    """
    Phase 6: Synthesize a meta-plan from a history of successful execution trees.
    """
    def __init__(self, llm: Llm, prompt_loader: Optional[PromptLoader] = None):
        self.llm = llm
        self.prompt_loader = prompt_loader or PromptLoader()
        self.registry = SkillRegistry()

    async def synthesize(
        self,
        skill_id: str,
        combined_signature: str,
        primary_action: str,
        primary_object: str,
        recent_trees: List[Dict[str, Any]]
    ) -> Optional[SkillManifest]:
        """
        Génère un SkillManifest en condensant les récents arbres d'exécution,
        puis l'enregistre en DRAFT puis SHADOW.
        """
        if len(recent_trees) == 0:
            Logger.warning(f"[SkillSynthesizer] Aucun arbre fourni pour la synthèse de {skill_id}.")
            return None

        prompt = self.prompt_loader.load(
            "skill_synthesis.md",
            combined_signature=combined_signature,
            trees=recent_trees
        )

        try:
            Logger.info(f"[SkillSynthesizer] 🧠 Démarrage de la synthèse (LLM) pour {skill_id}...")
            synthesis: SkillSynthesisResult = await self.llm.generate_structured(
                prompt=prompt,
                schema=SkillSynthesisResult,
                tag="SkillSynthesis"
            )
            Logger.info(f"[SkillSynthesizer] ✅ Synthèse réussie pour {skill_id}.")
        except Exception as e:
            Logger.error(f"[SkillSynthesizer] Échec de la synthèse LLM pour {skill_id} : {e}")
            return None

        # Construction du JSON Schema des paramètres d'entrée
        parameters_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        for param_name, param_def in synthesis.dynamic_parameters.items():
            parameters_schema["properties"][param_name] = {
                "type": param_def.type,
                "description": param_def.description
            }
            parameters_schema["required"].append(param_name)

        # Construction du SkillManifest
        manifest = SkillManifest(
            skill_id=skill_id,
            namespace="desktop",
            name=f"{primary_action.capitalize()} {primary_object}",
            description=synthesis.description,
            parameters_schema=parameters_schema,
            signature_hashes=[f"sig:{primary_action}:{primary_object}"],
            environment=ExecutionEnvironment()
        )

        # Flow Payload contient l'action de base + le méta-plan
        flow_payload = {
            "action": primary_action,
            "object": primary_object,
            "meta_plan": [node.model_dump(exclude_none=True) if hasattr(node, "model_dump") else node.dict(exclude_none=True) for node in synthesis.meta_plan]
        }

        # Extraction de la carte d'identité du modèle créateur
        model_id = getattr(self.llm, "model_id", "unknown")
        capabilities = []
        reasoning_score = 1.0
        benchmark_score = 50.0

        if hasattr(self.llm, "runtime_state") and self.llm.runtime_state:
            prov_mgr = getattr(self.llm.runtime_state, "provider_manager", None)
            if prov_mgr and hasattr(prov_mgr, "get_model_metadata"):
                meta = prov_mgr.get_model_metadata(model_id)
                if meta:
                    capabilities = meta.capabilities
                    reasoning_score = meta.reasoning_score
                    benchmark_score = meta.benchmark_score

        # Enregistrement dans la base
        self.registry.create_draft_skill(
            manifest=manifest,
            flow_payload=flow_payload,
            creator_model=model_id,
            creator_capabilities=capabilities,
            min_reasoning_score=reasoning_score,
            min_benchmark_score=benchmark_score
        )

        self.registry.transition_state(
            skill_id=skill_id,
            version=1,
            target_state=SkillState.SHADOW,
            reason="Skill auto-synthétisé via SkillSynthesizer et passé en SHADOW."
        )

        self.registry.index_signature(f"sig:{primary_action}:{primary_object}", skill_id, target_app=primary_object)
        
        Logger.info(f"[SkillSynthesizer] 🚀 Skill '{skill_id}' généré avec succès en SHADOW !")
        return manifest

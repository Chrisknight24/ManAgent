import asyncio
import json
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from core.llm import Llm
from core.skills.models import SkillManifest, SkillVersion, SkillState, FailureBundle, BreakoutReport
from core.skills.registry import SkillRegistry
from utils.logger import Logger
from core.prompt_loader import PromptLoader
from core.skills.synthesizer import SynthesizedPlanNode

class SkillRepairResult(BaseModel):
    repair_reason: str = Field(..., description="Explication courte de la correction apportée")
    meta_plan: List[SynthesizedPlanNode] = Field(default_factory=list, description="Le nouveau méta-plan complet avec la correction intégrée")

class SkillRepairEngine:
    def __init__(self, llm: Llm, prompt_loader: Optional[PromptLoader] = None):
        self.llm = llm
        self.prompt_loader = prompt_loader
        self.registry = SkillRegistry()

    async def repair_skill(
        self,
        skill_id: str,
        failed_version: int,
        failure_bundle: Optional[FailureBundle] = None,
        breakout_report: Optional[BreakoutReport] = None
    ):
        """
        Tente de réparer un Skill tombé en QUARANTINE en générant une nouvelle version (vN+1) en DRAFT.
        """
        Logger.info(f"[SkillRepairEngine] 🔧 Démarrage de l'analyse et réparation pour '{skill_id}' v{failed_version}...")
        
        # 1. Récupération de l'ancien payload et manifeste
        manifest = self.registry.get_skill(skill_id)
        if not manifest:
            Logger.error(f"[SkillRepairEngine] Impossible de réparer: Skill '{skill_id}' introuvable.")
            return

        old_payload = self.registry.get_flow_payload(skill_id, failed_version)
        if not old_payload or "meta_plan" not in old_payload:
            Logger.error(f"[SkillRepairEngine] Impossible de réparer: Méta-Plan original introuvable pour v{failed_version}.")
            return

        old_meta_plan = old_payload.get("meta_plan", [])
        action = old_payload.get("action", "unknown")
        obj = old_payload.get("object", "unknown")

        # Extraction des infos d'erreur
        error_msg = "Échec inexpliqué."
        failed_step = "unknown"
        if breakout_report:
            error_msg = breakout_report.error_message
            failed_step = breakout_report.failed_checkpoint_id
        elif failure_bundle and failure_bundle.breakout_report:
            error_msg = failure_bundle.breakout_report.error_message
            failed_step = failure_bundle.breakout_report.failed_checkpoint_id

        if not self.prompt_loader:
            from core.prompt_loader import get_prompt_loader
            self.prompt_loader = get_prompt_loader()

        prompt = self.prompt_loader.load(
            "skill_repair.md",
            skill_id=skill_id,
            action=action,
            obj=obj,
            old_meta_plan=old_meta_plan,
            error_msg=error_msg,
            failed_step=failed_step,
            parameters=failure_bundle.parameters_used if failure_bundle else {}
        )

        try:
            repair_result: SkillRepairResult = await self.llm.generate_structured(
                prompt=prompt,
                schema=SkillRepairResult,
                tag="SkillRepair"
            )
        except Exception as e:
            Logger.error(f"[SkillRepairEngine] ❌ Échec de la génération LLM pour la réparation: {e}")
            return

        # 3. Création de la version vN+1 en DRAFT
        new_payload = {
            "action": action,
            "object": obj,
            "meta_plan": [node.dict(exclude_none=True) for node in repair_result.meta_plan]
        }
        
        # L'enregistrement DRAFT crée la vN+1
        new_version_obj = self.registry.create_repair_version(
            flow_payload_ref=f"payload_{skill_id}_v{failed_version+1}",
            payload_content=json.dumps(new_payload),

            skill_id=skill_id,
            
            creator_model=self.llm.model_id,
            parent_version=failed_version,
            repair_reason=repair_result.repair_reason
        )

        new_version_num = new_version_obj.version
        if not new_version_num:
            Logger.error(f"[SkillRepairEngine] ❌ Échec de l'enregistrement de la nouvelle version.")
            return

        # 4. Passage immédiat en SHADOW pour qualification
        self.registry.transition_state(
            skill_id=skill_id,
            version=new_version_num,
            target_state=SkillState.SHADOW,
            reason="Skill auto-réparé. Début de la qualification SHADOW."
        )

        Logger.info(f"[SkillRepairEngine] ✅ Réparation réussie. Nouvelle version v{new_version_num} propulsée en SHADOW.")


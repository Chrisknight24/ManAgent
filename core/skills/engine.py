"""
core.skills.engine
==================
Moteur d'exécution des Skills de ManAgent au runtime.
Assure :
1. L'exécution déterministe ou Shadow d'un Skill (production vs évaluation passive).
2. Le suivi et la validation des Checkpoints sémantiques.
3. La détection et le confinement immédiat des dérives / anomalies (Breakout).
4. La création de FailureBundle structuré en cas de rupture pour l'auto-réparation.
5. La remontée des métriques au SkillRegistry et la propagation d'événements.
"""

import time
import uuid
import asyncio
from typing import Dict, List, Optional, Any, Callable
from utils.logger import Logger
from core.constants import Events
from core.skills.models import (
    SkillManifest,
    SkillVersion,
    SkillState,
    Checkpoint,
    BreakoutReport,
    FailureBundle,
    FailureClass,
)
from core.skills.registry import SkillRegistry
from transport.packet_models import (
    CheckpointReachedEvent,
    BreakoutOccurredEvent,
    ExecutionCompletedEvent,
)


class SkillExecutionEngine:
    """
    Moteur d'exécution et de supervision en temps réel des Skills.
    Gère l'interfaçage avec l'hôte, le suivi des checkpoints et le repli automatique.
    """

    def __init__(
        self,
        registry: Optional[SkillRegistry] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], Any]] = None
    ):
        self.registry = registry or SkillRegistry()
        self.event_emitter = event_emitter

    async def _emit_event(self, event_name: str, data: Dict[str, Any]):
        """Émet un événement via le bus d'événements ou le callback."""
        if self.event_emitter:
            try:
                res = self.event_emitter(event_name, data)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                Logger.warning(f"[SkillExecutionEngine] Erreur émission événement '{event_name}': {e}")

    async def execute_skill(
        self,
        manifest: SkillManifest,
        version: SkillVersion,
        parameters: Dict[str, Any],
        host_executor: Callable[[str, Dict[str, Any]], Any],
        is_shadow: bool = False,
        mission_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exécute un Skill en production ou en mode Shadow.
        
        :param manifest: Manifeste du Skill
        :param version: Version à exécuter
        :param parameters: Paramètres passés au Skill
        :param host_executor: Fonction exécutant l'instruction ou le flux sur l'hôte
        :param is_shadow: Si True, exécution fantôme en tâche de fond pour qualification
        :param mission_id: Identifiant de la mission englobante
        :return: Dictionnaire résultat avec statut, checkpoints franchis et outputs
        """
        start_time = time.time()
        skill_id = manifest.skill_id
        ver_num = version.version
        m_id = mission_id or str(uuid.uuid4())
        
        mode_str = "👻 SHADOW" if is_shadow else "🚀 PRODUCTION"
        Logger.info(f"[SkillExecutionEngine] {mode_str} Exécution du Skill '{skill_id}' v{ver_num}")

        passed_checkpoints: List[str] = []
        context_state: Dict[str, Any] = {}

        try:
            # 1. Vérification des checkpoints définis dans le manifest
            checkpoints_map = {cp.checkpoint_id: cp for cp in manifest.checkpoints}
            
            # 2. Appel du host_executor pour lancer le flux ou la commande
            execution_response = await host_executor(version.flow_payload_ref, parameters)
            
            # L'hôte peut renvoyer un statut structuré ou un booléen
            if isinstance(execution_response, dict):
                is_success = execution_response.get("success", True)
                breakout_data = execution_response.get("breakout")
                output_data = execution_response.get("output", {})
                passed_cps = execution_response.get("passed_checkpoints", [])
            else:
                is_success = bool(execution_response)
                breakout_data = None
                output_data = {"result": execution_response}
                passed_cps = [cp.checkpoint_id for cp in manifest.checkpoints]

            # 3. Traitement des checkpoints franchis
            for cp_id in passed_cps:
                cp_obj = checkpoints_map.get(cp_id, Checkpoint(checkpoint_id=cp_id, name=cp_id))
                passed_checkpoints.append(cp_id)
                
                cp_event = CheckpointReachedEvent(
                    skill_id=skill_id,
                    version=ver_num,
                    checkpoint_id=cp_id,
                    checkpoint_name=cp_obj.name,
                    is_critical=cp_obj.is_critical,
                    reached_at=time.time(),
                    observed_state=output_data
                )
                await self._emit_event(Events.CHECKPOINT_REACHED, cp_event.__dict__ if hasattr(cp_event, '__dict__') else cp_event.dict())

            # 4. Détection et traitement d'un Breakout / Échec
            if not is_success or breakout_data:
                failed_cp_id = "unknown"
                if breakout_data and isinstance(breakout_data, dict):
                    failed_cp_id = breakout_data.get("failed_checkpoint_id", "unknown")
                    err_msg = breakout_data.get("error_message", "Breakout détecté pendant l'exécution")
                    f_class = FailureClass(breakout_data.get("failure_class", FailureClass.UNKNOWN.value))
                else:
                    err_msg = "Échec d'exécution du flux par l'hôte"
                    f_class = FailureClass.EXECUTION_ERROR

                breakout_report = BreakoutReport(
                    skill_id=skill_id,
                    version=ver_num,
                    failed_checkpoint_id=failed_cp_id,
                    completed_checkpoints=passed_checkpoints,
                    failure_class=f_class,
                    error_message=err_msg,
                    recoverability="HIGH" if len(passed_checkpoints) > 0 else "MEDIUM",
                    resume_context={"parameters": parameters, "passed_checkpoints": passed_checkpoints}
                )

                # Événement de rupture
                bo_event = BreakoutOccurredEvent(
                    skill_id=skill_id,
                    version=ver_num,
                    step_id=failed_cp_id,
                    breakout_type=f_class.value,
                    reason=err_msg,
                    occurred_at=time.time(),
                    context_snapshot=breakout_report.to_dict(),
                    suggested_action="FALLBACK_TO_SOLVER"
                )
                await self._emit_event(Events.BREAKOUT_OCCURRED, bo_event.__dict__ if hasattr(bo_event, '__dict__') else bo_event.dict())

                # Enregistrement métrique négative au Registry
                self.registry.record_run_metric(
                    skill_id=skill_id,
                    version=ver_num,
                    success=False,
                    is_breakout=True,
                    is_shadow=is_shadow
                )

                # Construction du FailureBundle pour l'auto-réparation
                failure_bundle = FailureBundle(
                    incident_id=str(uuid.uuid4()),
                    mission_id=m_id,
                    skill_id=skill_id,
                    version=ver_num,
                    breakout_report=breakout_report,
                    timestamp=time.time(),
                    parameters_used=parameters,
                    host_environment_snapshot=manifest.environment.to_dict()
                )

                duration_ms = (time.time() - start_time) * 1000.0
                await self._emit_event(Events.EXECUTION_COMPLETED, {
                    "skill_id": skill_id,
                    "version": ver_num,
                    "success": False,
                    "duration_ms": duration_ms,
                    "error_message": err_msg,
                    "passed_checkpoints": passed_checkpoints
                })

                return {
                    "success": False,
                    "breakout": True,
                    "breakout_report": breakout_report,
                    "failure_bundle": failure_bundle,
                    "passed_checkpoints": passed_checkpoints,
                    "duration_ms": duration_ms
                }

            # 5. Succès complet de l'exécution
            duration_ms = (time.time() - start_time) * 1000.0
            self.registry.record_run_metric(
                skill_id=skill_id,
                version=ver_num,
                success=True,
                is_breakout=False,
                is_shadow=is_shadow
            )

            completed_event = ExecutionCompletedEvent(
                skill_id=skill_id,
                version=ver_num,
                success=True,
                duration_ms=duration_ms,
                total_steps=len(passed_checkpoints),
                passed_checkpoints=passed_checkpoints,
                output_data=output_data
            )
            await self._emit_event(Events.EXECUTION_COMPLETED, completed_event.__dict__ if hasattr(completed_event, '__dict__') else completed_event.dict())

            return {
                "success": True,
                "breakout": False,
                "output": output_data,
                "passed_checkpoints": passed_checkpoints,
                "duration_ms": duration_ms
            }

        except Exception as e:
            Logger.error(f"[SkillExecutionEngine] Exception non gérée lors de l'exécution du skill '{skill_id}': {e}")
            duration_ms = (time.time() - start_time) * 1000.0
            self.registry.record_run_metric(
                skill_id=skill_id,
                version=ver_num,
                success=False,
                is_breakout=True,
                is_shadow=is_shadow
            )
            return {
                "success": False,
                "breakout": True,
                "error_message": str(e),
                "passed_checkpoints": passed_checkpoints,
                "duration_ms": duration_ms
            }

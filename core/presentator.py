"""
core/presentator.py
===================
Entité spécialisée dans la reformulation et la présentation des résultats de mission.
Prend le contexte technique brut et le registre pour en faire un rapport clair pour l'utilisateur.
Mise à jour v2 : retour structuré (rapport + résumé) en un seul appel LLM.
"""

import asyncio
from typing import Optional
from pydantic import BaseModel, Field
from core.entity import Entity
from core.llm import Llm
from utils.logger import Logger
from core.prompt_loader import get_prompt_loader
from core.i18n import _


class PresentatorOutput(BaseModel):
    """Structure de retour du Presentator pour une mission."""
    user_report: str = Field(..., description="Message long et détaillé à destination de l'utilisateur.")
    summary: str = Field(..., description="Résumé court (1-2 phrases) de l'action clé menée et du résultat principal.")


class Presentator(Entity):
    def __init__(self, provider_manager, runtime_state,
             provider_id: str = None, model_id: str = None, llm: Optional[Llm] = None):
        
        super().__init__(name="presentator", role="presenter", llm=llm, parent=None)
        
        self.provider_manager = provider_manager
        self.runtime_state = runtime_state

        if not self.llm and provider_id and model_id:
            self.llm = Llm(
                provider_manager=provider_manager,
                provider_id=provider_id,
                model_id=model_id,
                runtime_state=runtime_state  # <--- NOUVEAU
            )

        if not self.llm:
            raise ValueError(_("Presentator requires a Llm instance or provider/model identifiers."))
    # ============================================================
    # IMPLÉMENTATION DE LA MÉTHODE ABSTRAITE DE Entity
    # ============================================================

    async def process(self, *args, **kwargs) -> str:
        Logger.info("[Presentator] 🎤 Appel via process() – délégation vers generate_mission_output")
        goal = kwargs.get("goal", args[0] if args else "")
        final_context = kwargs.get("final_context", args[1] if len(args) > 1 else "")
        variable_registry = kwargs.get("variable_registry", args[2] if len(args) > 2 else {})
        accumulated_response = kwargs.get("accumulated_response", args[3] if len(args) > 3 else "")
        mission_status = kwargs.get("mission_status", "success")
        error_reason = kwargs.get("error_reason", None)

        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            mission_status=mission_status,
            error_reason=error_reason
        )
        return output.user_report
    # ============================================================
    # NOUVELLE MÉTHODE STRUCTURÉE (PHASE 1)
    # ============================================================

    async def generate_mission_output(
        self,
        goal: str,
        final_context: str,
        variable_registry: dict,
        accumulated_response: str,
        mission_status: str,  # "success" ou "failed"
        error_reason: Optional[str] = None,
        mission_id: Optional[str] = None,  # <--- AJOUT
    ) -> PresentatorOutput:
        """
        Génère un rapport utilisateur long ET un résumé court, en un seul appel LLM structuré.
        En cas d'échec, retourne un fallback construit manuellement avec un message explicite.
        """
        Logger.info(f"[Presentator] 📝 Génération structurée (rapport + résumé) pour statut: {mission_status}")

        # Récupération du mood (si disponible)
        mood = None
        if hasattr(self.runtime_state, 'session_memory') and self.runtime_state.session_memory:
            mood = self.runtime_state.session_memory.context.mood

        detail_level = getattr(self.runtime_state, "presentator_detail_level", "brief")

        loader = get_prompt_loader()
        prompt = loader.load(
            "presentator_output.md",
            lang=self.runtime_state.language,
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            mission_status=mission_status,
            error_reason=error_reason or "",
            session_mood=mood or "neutre",
            detail_level=detail_level
        )

        try:
            # --- NOUVEAU : plus de paramètre mission_id, tout est automatique ---
            output: PresentatorOutput = await self.llm.generate_structured(
                prompt=prompt,
                schema=PresentatorOutput,
                tag="Presentator_output",
                mission_id=mission_id  # <--- TRANSMISSION EXPLICITE
            )
            Logger.info("[Presentator] ✅ Génération structurée réussie.")
            return output

        except asyncio.TimeoutError as e:
            Logger.error(f"[Presentator] ⏰ Timeout lors de la génération structurée : {e}")
            return self._build_fallback_output(goal, mission_status, error_reason, _("Timeout du service d'IA"))

        except Exception as e:
            Logger.error(f"[Presentator] ❌ Échec de la génération structurée : {e}")
            return self._build_fallback_output(goal, mission_status, error_reason, str(e))
        
                
    def _build_fallback_output(self, goal: str, mission_status: str, error_reason: Optional[str], error_detail: str) -> PresentatorOutput:
        """Construit un rapport et un résumé minimal de secours."""
        if mission_status == "success":
            user_report = _("✅ Mission '{goal}' accomplie techniquement, mais le rapport détaillé n'a pas pu être généré en raison d'une indisponibilité du service d'IA (détail : {detail}). Veuillez consulter les logs pour plus d'informations.").format(goal=goal, detail=error_detail)
            summary = _("Mission '{goal}' : succès technique (rapport indisponible).").format(goal=goal)
        else:
            reason = error_reason or _("cause inconnue")
            user_report = _("❌ La mission '{goal}' a échoué. Raison : {reason}. Le rapport détaillé est indisponible (indisponibilité du service d'IA : {detail}).").format(goal=goal, reason=reason, detail=error_detail)
            summary = _("Mission '{goal}' : échec – {reason} (rapport indisponible).").format(goal=goal, reason=reason)
        
        Logger.warning(f"[Presentator] ⚠️ Fallback utilisé pour la mission '{goal}'. Raison : {error_detail}")
        return PresentatorOutput(user_report=user_report, summary=summary)

    # ============================================================
    # ANCIENNES MÉTHODES CONSERVÉES POUR COMPATIBILITÉ
    # ============================================================

    async def generate_mission_report(self, goal: str, final_context: str, variable_registry: dict, accumulated_response: str) -> str:
        """Ancienne méthode conservée pour ne pas casser d'éventuels appels externes."""
        Logger.info("[Presentator] 🎤 [LEGACY] Génération du rapport long uniquement.")
        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            mission_status="success"
        )
        return output.user_report

    async def generate_error_report(self, goal: str, error_reason: str, final_context: str) -> str:
        """Ancienne méthode d'erreur conservée pour compatibilité."""
        Logger.info("[Presentator] 📝 [LEGACY] Génération du rapport d'échec long uniquement.")
        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry={},
            accumulated_response="",
            mission_status="failed",
            error_reason=error_reason
        )
        return output.user_report
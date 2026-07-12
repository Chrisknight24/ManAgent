"""
core/presentator.py
===================
Entité spécialisée dans la reformulation et la présentation des résultats de mission.
Prend le contexte technique brut et le registre pour en faire un rapport clair pour l'utilisateur.
"""

from typing import Optional
from core.entity import Entity
from core.llm import Llm
from utils.logger import Logger
from core.prompt_loader import get_prompt_loader
from core.i18n import _

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
                model_id=model_id
            )

        if not self.llm:
            raise ValueError(_("Presentator requires a Llm instance or provider/model identifiers."))

    async def generate_mission_report(self, goal: str, final_context: str, variable_registry: dict, accumulated_response: str) -> str:
        """
        Analyse les traces d'exécution, le registre et les réponses intermédiaires 
        pour formuler un rapport structuré et professionnel.
        """
        Logger.info("[Presentator] 🎤 Analyse de l'exécution et rédaction du rapport final...")

        # --- Conseil pour le Presentator : SES PROPRES leçons (entity_type="Presentator"),
        # pas celles du Planner. Enveloppé dans son propre try/except : le RAG ne doit jamais
        # faire échouer la génération du rapport final, qui est la donnée la plus visible pour
        # l'utilisateur — un échec ici dégradait auparavant TOUT le rapport (fallback technique).
        advice = ""
        if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
            try:
                advice = await self.runtime_state.learner.get_advice(entity_types=["Presentator"], goal=goal)
            except Exception as e:
                Logger.error(f"[Presentator] Erreur récupération conseils (non bloquant) : {e}")
                advice = ""
            if advice:
                Logger.debug("[Presentator] Conseils reçus pour le rapport.")
            else:
                Logger.debug("[Presentator] Aucun conseil reçu pour le rapport.")

        # Récupérer le mood depuis le SessionContext (si disponible)
        mood = None
        if hasattr(self.runtime_state, 'session_memory') and self.runtime_state.session_memory:
            mood = self.runtime_state.session_memory.context.mood

        loader = get_prompt_loader()
        prompt = loader.load(
            "presentator_report.md",
            lang=self.runtime_state.language,
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            advice=advice,  # <--- injection
            session_mood=mood  # <-- injection
        )
        try:
            report = await self.llm.generate_text(prompt=prompt, tag="Presentator_report")
            return report.strip()
        except Exception as e:
            Logger.error(f"[Presentator] ⚠️ Impossible de générer le rapport sémantique. Motif : {e}")
            raise e

    async def process(self, goal: str, final_context: str, variable_registry: dict, accumulated_response: str) -> str:
        """
        Implémentation obligatoire de la méthode abstraite Entity.process.
        Redirige vers la logique de génération du rapport.
        """
        return await self.generate_mission_report(goal, final_context, variable_registry, accumulated_response)

    async def generate_error_report(self, goal: str, error_reason: str, final_context: str) -> str:
        """
        Génère un message d'échec clair, structuré et professionnel pour l'utilisateur.
        """
        Logger.info("[Presentator] 📝 Génération du rapport d'échec...")

        # --- Conseil pour le Presentator (version échec) : mêmes principes que generate_mission_report ---
        advice = ""
        if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
            try:
                advice = await self.runtime_state.learner.get_advice(entity_types=["Presentator"], goal=goal)
            except Exception as e:
                Logger.error(f"[Presentator] Erreur récupération conseils (non bloquant) : {e}")
                advice = ""
            if advice:
                Logger.debug("[Presentator] Conseils reçus pour le rapport d'échec.")
            else:
                Logger.debug("[Presentator] Aucun conseil reçu pour le rapport d'échec.")

        # Récupérer le mood depuis le SessionContext (si disponible)
        mood = None
        if hasattr(self.runtime_state, 'session_memory') and self.runtime_state.session_memory:
            mood = self.runtime_state.session_memory.context.mood

        loader = get_prompt_loader()
        prompt = loader.load(
            "presentator_error.md",
            lang=self.runtime_state.language,
            goal=goal,
            error_reason=error_reason,
            final_context=final_context,
            advice=advice,  # <--- injection
            session_mood=mood  # <-- injection
        )
        try:
            report = await self.llm.generate_text(prompt=prompt, tag="Presentator_error")
            return report.strip()
        except Exception as e:
            Logger.error(f"[Presentator] ⚠️ Échec de génération du rapport d'erreur : {e}")
            raise
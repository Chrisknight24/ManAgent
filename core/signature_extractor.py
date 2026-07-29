"""
core/signature_extractor.py
===========================
Extraction de missions simples (signatures) à partir d'un goal et d'un contexte.
Utilise le LLM du solver appelant pour générer une liste de MissionSignature.
"""

from typing import List
from pydantic import BaseModel, Field
from core.llm import Llm
from core.plan_models import MissionSignature
from core.prompt_loader import get_prompt_loader
from utils.logger import Logger


class SignatureList(BaseModel):
    """Conteneur pour la liste des signatures extraites."""
    signatures: List[MissionSignature] = Field(
        default_factory=list,
        description="Liste des missions simples extraites du goal."
    )


class SignatureExtractor:
    """
    Entité LLM dédiée à l'extraction de signatures pour un solver donné.
    N'hérite pas de Entity car elle n'a pas besoin de hiérarchie, juste d'un LLM.
    """

    def __init__(self, llm: Llm, runtime_state):
        self.llm = llm
        self.runtime_state = runtime_state

    async def extract(self, goal: str, context: str = "") -> List[MissionSignature]:
        """
        Extrait les signatures d'une mission à partir de son objectif.
        """
        Logger.debug(f"[SignatureExtractor] Extraction pour goal: {goal[:50]}...")

        # Récupération du mission_id depuis le contexte
        mission_id = None
        if self.runtime_state and hasattr(self.runtime_state, 'execution_context'):
            mission_id = self.runtime_state.execution_context.get("mission_id")

        loader = get_prompt_loader()
        prompt = loader.load(
            "signature_extractor.md",
            lang=getattr(self.runtime_state, "language", "en"),
            goal=goal,
            context=context
        )

        try:
            result = await self.llm.generate_structured(
                prompt=prompt,
                schema=SignatureList,
                tag="SignatureExtractor",
                mission_id=mission_id  # <--- TRANSMISSION EXPLICITE
            )
            return result.signatures
        except Exception as e:
            Logger.error(f"[SignatureExtractor] Échec de l'extraction : {e}")
            return []
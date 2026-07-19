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

    async def extract(self, goal: str, context: str) -> List[MissionSignature]:
        """
        Extrait les signatures (action + objet + desired_state) à partir du goal et du contexte.
        Retourne une liste de MissionSignature.
        """
        Logger.debug(f"[SignatureExtractor] Extraction pour goal: {goal[:50]}...")

        loader = get_prompt_loader()
        prompt = loader.load(
            "signature_extractor.md",
            lang=self.runtime_state.language,
            goal=goal,
            context=context
        )

        result = await self.llm.generate_structured(
            prompt=prompt,
            schema=SignatureList,
            tag="SignatureExtractor"
        )
        Logger.info(f"[SignatureExtractor] {len(result.signatures)} signature(s) extraite(s).")
        return result.signatures
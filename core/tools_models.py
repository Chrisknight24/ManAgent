"""
core/tools_models.py
====================
Modèles Pydantic pour le ToolsManager.
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from core.i18n import _
from core.base_schema import BaseDiscoverySchema


class ToolDecision(BaseDiscoverySchema):
    """
    Décision du ToolsManager : quel outil interne appeler et avec quels arguments.
    Si success est False, tool_name et tool_args_json ne sont pas utilisés.
    """
    success: bool = Field(
        ...,
        description=_("True si un outil disponible correspond à la requête, False sinon.")
    )
    tool_name: Optional[str] = Field(
        None,
        description=_("Nom de l'outil interne à appeler (si success=True).")
    )
    tool_args_json: str = Field(
        default="{}",
        description=_("Chaîne JSON contenant les arguments pour l'outil (si success=True).")
    )
    rejection_reason: Optional[str] = Field(
        None,
        description=_("Texte explicatif si success=False (ex: Raison du refus, paramètre manquant, variable introuvable).")
    )
    message: Optional[str] = Field(
        None,
        description=_("Alias pour rejection_reason si disponible.")
    )


class AnalysisResult(BaseModel):
    """
    Résultat de l'analyse d'une donnée par un LLM.
    Utilisé par l'outil interne `llm_analyze_data`.
    """
    success: bool = Field(
        ...,
        description=_("True si l'analyse a réussi (le LLM a pu répondre), False sinon.")
    )
    data: Optional[Any] = Field(
        None,
        description=_("La réponse de l'analyse (peut être une chaîne, un nombre, une liste, etc.).")
    )
    error_reason: Optional[str] = Field(
        None,
        description=_("Message complémentaire (ex: raison de l'échec).")
    )
    message: Optional[str] = Field(
        None,
        description=_("Explication complémentaire ou raison de l'échec (compatible avec les deux dénominations).")
    )

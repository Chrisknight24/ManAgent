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
    message: Optional[str] = Field(
        None,
        description=_("Message complémentaire (ex: raison de l'échec).")
    )
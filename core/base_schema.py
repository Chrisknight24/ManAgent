"""
core/base_schema.py
===================
Classe de base pour tous les schémas de sortie structurés.
Permet au LLM de demander une investigation (Progressive Disclosure)
en remplissant le champ discovery_request.
"""

from typing import Optional
from pydantic import BaseModel, Field
from core.discovery.models import DiscoveryRequest
from core.i18n import _


class BaseDiscoverySchema(BaseModel):
    """
    Classe de base pour tous les schémas de sortie structurés.
    Ajoute un champ optionnel discovery_request pour la Progressive Disclosure.
    """

    discovery_request: Optional[DiscoveryRequest] = Field(
    None,
    description=_(
        "Remplissez ce champ UNIQUEMENT si les métadonnées disponibles "
        "ne suffisent pas à répondre et que vous avez besoin d'investiguer "
        "une donnée précise (ex: valeur d'une variable, historique d'une mission). "
        "Indiquez alors le type de données (data_type), la cible (target) "
        "et le goal technique (technical_goal) correspondant. "
        "Si les données disponibles suffisent, laissez ce champ à None."
    )
)
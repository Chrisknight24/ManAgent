"""
entity.py
=========
Définit le concept d'Entité (Employé) dans l'architecture d'Entreprise.
Version corrigée : retrait de l'activation automatique de la Progressive Disclosure,
ajout de get_data_context pour le partage implicite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Any, Dict
import uuid
from utils.logger import Logger
from core.i18n import _


class Entity(ABC):
    """
    Classe de base abstraite pour tous les composants de l'Entreprise
    (Orchestrator, Planner, Solver, Memory, etc.).
    Implémente le pattern Composite (Hiérarchie Parent/Enfant).
    """

    def __init__(self, name: str, role: str, llm: Optional[Llm] = None, parent: Optional[Entity] = None):
        self.name = name
        self.role = role
        self.llm = llm
        self.parent = parent
        self.entity_id = str(uuid.uuid4())

        # --- DataProviders pour la Progressive Disclosure ---
        self._data_providers: Dict[str, DataProvider] = {}

        self.log("INFO", f"Recrutement acté (Rôle: {self.role}, IA: {'Oui' if self.llm else 'Non'}, ID: {self.entity_id[:8]})")

        # L'activation de la Progressive Disclosure est désormais déléguée aux entités filles
        # après l'initialisation complète de runtime_state et des DataProviders.

    # =====================================================
    # GESTION DES DATAPROVIDERS
    # =====================================================

    def register_data_provider(self, name: str, provider: DataProvider) -> None:
        """
        Enregistre un DataProvider pour cette entité.
        Le provider sera exposé au LLM pour la Progressive Disclosure.
        """
        self._data_providers[name] = provider
        if self.llm and self.llm._discovery_enabled:
            self.llm.update_discovery_providers(self._data_providers)
        Logger.debug(
            _("[Entity] DataProvider '{name}' enregistré pour {entity} (ID: {id})")
            .format(name=name, entity=self.name, id=self.entity_id[:8])
        )

    def get_data_providers(self) -> Dict[str, DataProvider]:
        """Retourne tous les DataProviders de l'entité."""
        return self._data_providers

    def get_data_provider(self, name: str) -> Optional[DataProvider]:
        """Retourne un DataProvider spécifique par son nom."""
        return self._data_providers.get(name)

    # =====================================================
    # CONTEXTE DE DONNÉES POUR LA PROGRESSIVE DISCLOSURE
    # =====================================================

    def get_data_context(self) -> Any:
        """
        Retourne le contexte de données que cette entité souhaite partager
        avec le Discovery Framework pour la Progressive Disclosure.
        Par défaut, retourne None. Les entités filles peuvent surcharger
        pour fournir leur registre, historique, etc.
        """
        return None

    # =====================================================
    # OBSERVABILITÉ & HIÉRARCHIE
    # =====================================================

    def log(self, level: str, message: str):
        """Standardise les logs avec l'identité de l'Entité."""
        formatted_message = f"[{self.name}] {message}"
        if level.upper() == "INFO":
            Logger.info(formatted_message)
        elif level.upper() == "WARNING":
            Logger.warning(formatted_message)
        elif level.upper() == "ERROR":
            Logger.error(formatted_message)
        else:
            Logger.debug(formatted_message)

    def log_event(self, event_type: str, **fields):
        Logger.event(
            event_type,
            entity_name=self.name,
            entity_role=self.role,
            entity_id=self.entity_id,
            **fields
        )

    def get_root(self) -> Entity:
        """Remonte la chaîne hiérarchique jusqu'au PDG (Root)."""
        current = self
        while current.parent is not None:
            current = current.parent
        return current

    # =====================================================
    # GESTION DU CERVEAU (LLM)
    # =====================================================

    def has_brain(self) -> bool:
        return self.llm is not None

    def get_brain(self) -> Llm:
        if not self.llm:
            raise RuntimeError(_("L'entité '{name}' n'est pas équipée d'un LLM.").format(name=self.name))
        return self.llm

    def set_llm(self, new_llm: Llm):
        """Permet de changer de moteur cognitif à la volée (ex: Fallback)."""
        self.llm = new_llm
        self.log("INFO", _("Nouveau moteur cognitif assigné ({provider}/{model}).").format(
            provider=new_llm.provider_id,
            model=new_llm.model_id
        ))

    # =====================================================
    # LE CONTRAT DE TRAVAIL
    # =====================================================

    @abstractmethod
    async def process(self, *args, **kwargs) -> Any:
        """
        La fonction métier principale de l'entité.
        """
        pass


# Imports différés pour éviter les circularités
from core.llm import Llm
from core.discovery.data_provider import DataProvider
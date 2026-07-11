"""
entity.py
=========
Définit le concept d'Entité (Employé) dans l'architecture d'Entreprise.
"""

from abc import ABC, abstractmethod
from typing import Optional, Any
from utils.logger import Logger
from core.llm import Llm

class Entity(ABC):
    """
    Classe de base abstraite pour tous les composants de l'Entreprise 
    (Orchestrator, Planner, Solver, Memory, etc.).
    Implémente le pattern Composite (Hiérarchie Parent/Enfant).
    """

    def __init__(self, name: str, role: str, llm: Optional[Llm] = None, parent: Optional['Entity'] = None):
        self.name = name
        self.role = role
        self.llm = llm
        self.parent = parent
        
        self.log("INFO", f"Recrutement acté (Rôle: {self.role}, IA: {'Oui' if self.llm else 'Non'})")

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
        """
        Proxy vers Logger.event() qui attache automatiquement l'identité de l'entité
        (nom + rôle) — évite de répéter ces deux champs à chaque appel, et garantit que
        la future couche d'observabilité peut toujours répondre à "quelle entité a fait
        ça ?" sans ambiguïté.
        """
        Logger.event(event_type, entity_name=self.name, entity_role=self.role, **fields)

    def get_root(self) -> 'Entity':
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
            raise RuntimeError(f"L'entité '{self.name}' n'est pas équipée d'un LLM.")
        return self.llm

    def set_llm(self, new_llm: Llm):
        """Permet de changer de moteur cognitif à la volée (ex: Fallback)."""
        self.llm = new_llm
        self.log("INFO", f"Nouveau moteur cognitif assigné ({new_llm.provider_id}/{new_llm.model_id}).")

    # =====================================================
    # LE CONTRAT DE TRAVAIL
    # =====================================================

    @abstractmethod
    async def process(self, *args, **kwargs) -> Any:
        """
        La fonction métier principale de l'entité.
        """
        pass
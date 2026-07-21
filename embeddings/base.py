"""
embeddings/base.py
==================
Classe abstraite pour les fournisseurs d'embedding.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Callable, Awaitable, Any


class EmbeddingProvider(ABC):
    """Contrat pour un fournisseur d'embedding."""

    @abstractmethod
    async def initialize(self) -> None:
        """Charge le modèle en mémoire. Peut être long (téléchargement)."""
        pass

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Retourne l'embedding d'un texte."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Retourne les embeddings d'une liste de textes."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimension de l'espace vectoriel."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifiant unique du modèle (ex: 'paraphrase-multilingual-MiniLM-L12-v2')."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Nom affichable pour le frontend."""
        pass

    @property
    def is_loaded(self) -> bool:
        """Indique si le modèle est chargé en mémoire (peut être surchargé)."""
        return True
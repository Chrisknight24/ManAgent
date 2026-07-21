"""
embeddings/__init__.py
======================
Package pour la gestion des embeddings.
"""

from embeddings.base import EmbeddingProvider
from embeddings.manager import EmbeddingProviderManager

__all__ = ["EmbeddingProvider", "EmbeddingProviderManager"]
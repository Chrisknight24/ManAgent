"""
embeddings/providers/__init__.py
================================
Providers concrets pour les embeddings.
"""

from embeddings.providers.sentence_transformer import SentenceTransformerProvider

__all__ = ["SentenceTransformerProvider"]
"""
embeddings/providers/hash_provider.py
=====================================
Provider lite : embedding deterministe par hachage, zero dependance lourde.
But : faire tourner ManAgent sans torch (exe leger, offline).
Precision moindre que SentenceTransformer, mais jamais de crash.
"""
import hashlib
import math
import re
from typing import List

from embeddings.base import EmbeddingProvider


def hash_embed(text: str, dim: int = 256) -> List[float]:
    """Fonction pure reutilisable (aussi par core/embedding_service en fallback)."""
    if not text or not text.strip():
        return [0.0] * dim
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class HashEmbeddingProvider(EmbeddingProvider):
    """Provider lite enregistre par defaut. Aucun telechargement, aucune cle API."""

    PROVIDER_ID = "lite-hash"

    def __init__(self, dim: int = 256, display_name: str = "Lite Hash (offline)"):
        self._dim = dim
        self._display_name = display_name
        self._loaded = True  # rien a charger

    @property
    def model_name(self) -> str:
        return self.PROVIDER_ID

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_loaded(self) -> bool:
        return True

    async def initialize(self) -> None:
        return None

    async def embed(self, text: str) -> List[float]:
        return hash_embed(text, self._dim)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [hash_embed(t, self._dim) for t in texts]

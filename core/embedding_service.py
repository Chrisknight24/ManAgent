"""
core/embedding_service.py
==========================
SERVICE D'ENCODAGE SÉMANTIQUE

Ce fichier contient un "robot" (service) qui sait transformer du texte en
une liste de nombres (un vecteur). C'est ce qu'on appelle un embedding.

Le chargement du modèle est asynchrone (non bloquant) :
  - Le premier appel à embed() lance le chargement en arrière‑plan.
  - La mission continue de s'exécuter sans attendre le chargement.
  - Si un embed() est demandé pendant le chargement, il attend que le modèle soit prêt.
"""

import asyncio
from typing import List, Optional, TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
    _HAS_ST = True
except ImportError:
    _SentenceTransformer = None  # type: ignore
    _HAS_ST = False

from utils.logger import Logger


class EmbeddingService:
    """
    Service d'encodage sémantique.
    Utilise un modèle SentenceTransformer chargé en lazy et asynchrone.
    """

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self._model: Optional['SentenceTransformer'] = None
        self._loaded = False
        self._loading_event = asyncio.Event()      # Signal de chargement terminé
        self._load_task: Optional[asyncio.Task] = None

    def _load_model_sync(self) -> None:
        """
        Fonction synchrone qui charge réellement le modèle.
        Elle est exécutée dans un thread séparé.
        """
        if not _HAS_ST:
            raise ImportError(
                "sentence-transformers n'est pas installé. "
                "Veuillez l'installer avec : pip install sentence-transformers"
            )

        Logger.info(f"[EmbeddingService] Chargement du modèle {self.model_name} (asynchrone)...")
        try:
            self._model = _SentenceTransformer(self.model_name)
            self._loaded = True
            try:
                dim = self._model.get_embedding_dimension()
            except AttributeError:
                dim = self._model.get_sentence_embedding_dimension()
            Logger.info(f"[EmbeddingService] Modèle chargé avec succès (dim: {dim}).")
        except Exception as e:
            Logger.error(f"[EmbeddingService] Échec du chargement du modèle : {e}")
            raise
        finally:
            self._loading_event.set()

    async def _ensure_loaded(self) -> None:
        """
        Garantit que le modèle est chargé.
        Si le chargement est déjà en cours, on attend.
        Si aucun chargement n'est en cours, on le lance en arrière‑plan.
        """
        if self._loaded:
            return

        # Si le chargement est déjà en cours, on attend
        if self._load_task is not None and not self._load_task.done():
            Logger.debug("[EmbeddingService] Attente du chargement du modèle...")
            await self._loading_event.wait()
            return

        # Premier appel : on lance le chargement en arrière‑plan
        Logger.info("[EmbeddingService] Lancement du chargement du modèle en arrière‑plan...")
        loop = asyncio.get_running_loop()
        self._load_task = loop.run_in_executor(None, self._load_model_sync)
        await self._loading_event.wait()

    async def embed(self, text: str) -> List[float]:
        """
        Calcule l'embedding d'un texte en un vecteur de float.
        Le chargement du modèle est asynchrone : s'il n'est pas encore chargé,
        il est lancé en arrière‑plan, et cette fonction attend qu'il soit prêt.
        """
        if not text or not text.strip():
            Logger.warning("[EmbeddingService] Texte vide reçu, retour d'un vecteur nul.")
            return [0.0] * 384

        await self._ensure_loaded()

        # Maintenant le modèle est chargé
        embedding = self._model.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=True
        )[0]
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Calcule les embeddings pour une liste de textes.
        """
        if not texts:
            return []

        await self._ensure_loaded()
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True
        )
        return [emb.tolist() for emb in embeddings]

    @property
    async def dimension(self) -> int:
        """Retourne la dimension du modèle."""
        await self._ensure_loaded()
        if self._model is None:
            raise RuntimeError("Le modèle n'a pas pu être chargé.")
        try:
            return self._model.get_embedding_dimension()
        except AttributeError:
            return self._model.get_sentence_embedding_dimension()


# --- Singleton global avec gestion async ---
_embedding_service_instance: Optional[EmbeddingService] = None

def get_embedding_service() -> EmbeddingService:
    global _embedding_service_instance
    if _embedding_service_instance is None:
        _embedding_service_instance = EmbeddingService()
    return _embedding_service_instance


# --- Fonctions de confort async ---
async def embed_text(text: str) -> List[float]:
    return await get_embedding_service().embed(text)
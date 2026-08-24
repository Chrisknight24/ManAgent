"""
embeddings/providers/sentence_transformer.py
============================================
Provider pour les modèles SentenceTransformer locaux.
Calque la méthode d'import de core/embedding_service.py.
"""

import asyncio
from typing import List, Optional, Callable, Awaitable, TYPE_CHECKING

from utils.logger import Logger

# --- Import conditionnel (calqué sur embedding_service.py) ---
try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
    _HAS_ST = True
except ImportError:
    _SentenceTransformer = None  # type: ignore
    _HAS_ST = False

# Pour le type checking uniquement (forward reference)
if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

from embeddings.base import EmbeddingProvider


class SentenceTransformerProvider(EmbeddingProvider):
    """Provider pour les modèles SentenceTransformer (locaux)."""

    def __init__(
        self,
        model_id: str,
        display_name: Optional[str] = None,
        prefix_query: str = "",
        prefix_passage: str = "",
        emit_func: Optional[Callable[[str, dict], Awaitable[None]]] = None
    ):
        self._model_id = model_id
        self._display_name = display_name or model_id
        self._prefix_query = prefix_query
        self._prefix_passage = prefix_passage
        self._emit = emit_func
        # Annotation avec forward reference (guillemets) pour éviter l'erreur Pylance
        self._model: Optional["SentenceTransformer"] = None
        self._dimension: Optional[int] = None
        self._loaded = False

    @property
    def model_name(self) -> str:
        return self._model_id

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError(f"Modèle {self._model_id} non initialisé.")
        return self._dimension

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def initialize(self) -> None:
        """Charge le modèle. Envoie des événements de progression."""
        if self._loaded:
            return

        if not _HAS_ST:
            raise ImportError(
                "sentence-transformers n'est pas installé. "
                "Installez-le avec : pip install sentence-transformers"
            )

        Logger.info(f"[SentenceTransformerProvider] Démarrage chargement : {self._model_id}")

        if self._emit:
            await self._emit("EMBEDDING_MODEL_LOADING", {
                "model_id": self._model_id,
                "display_name": self._display_name,
                "status": "loading"
            })

        try:
            loop = asyncio.get_running_loop()
            # On utilise _SentenceTransformer (l'alias) pour le runtime
            self._model = await loop.run_in_executor(
                None,
                lambda: _SentenceTransformer(self._model_id)  # type: ignore
            )

            try:
                dim = self._model.get_embedding_dimension()
            except AttributeError:
                dim = self._model.get_sentence_embedding_dimension()

            self._dimension = dim
            self._loaded = True

            Logger.info(f"[SentenceTransformerProvider] Modèle chargé : {self._model_id} (dim={dim})")

            if self._emit:
                await self._emit("EMBEDDING_MODEL_LOADED", {
                    "model_id": self._model_id,
                    "display_name": self._display_name,
                    "status": "loaded",
                    "dimension": dim
                })

        except Exception as e:
            Logger.error(f"[SentenceTransformerProvider] Échec chargement {self._model_id} : {e}")
            if self._emit:
                await self._emit("EMBEDDING_MODEL_ERROR", {
                    "model_id": self._model_id,
                    "display_name": self._display_name,
                    "status": "error",
                    "error": str(e)
                })
            raise

    async def embed(self, text: str) -> List[float]:
        if not self._loaded:
            await self.initialize()
        if not text or not text.strip():
            return [0.0] * self._dimension

        prefixed_text = f"{self._prefix_query}{text}"

        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            None,
            lambda: self._model.encode(  # type: ignore
                [prefixed_text],
                convert_to_numpy=True,
                normalize_embeddings=True
            )[0]
        )
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not self._loaded:
            await self.initialize()
        if not texts:
            return []

        prefixed_texts = [
            f"{self._prefix_query}{t}" if t and t.strip() else t
            for t in texts
        ]

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._model.encode(  # type: ignore
                prefixed_texts,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
        )
        return [emb.tolist() for emb in embeddings]

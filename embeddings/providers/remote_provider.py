"""
embeddings/providers/remote_provider.py
========================================
Provider remote : embeddings via API compatible OpenAI (OpenAI, Groq, OpenRouter...).
Zero modele local, coute a l'usage (facturation API), ideal pour exe leger + precis.
"""
from typing import List, Optional

from embeddings.base import EmbeddingProvider
from utils.logger import Logger


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Appelle POST {base_url}/embeddings avec {"model", "input"}. Lazy import httpx."""

    def __init__(
        self,
        model_id: str = "text-embedding-3-small",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        display_name: Optional[str] = None,
    ):
        self._model_id = model_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._display_name = display_name or f"Remote:{model_id}"
        self._dimension: Optional[int] = None
        self._loaded = True  # pas de modele local a charger

    @property
    def model_name(self) -> str:
        return f"remote:{self._model_id}"

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError(f"Modele remote {self._model_id} pas encore utilise (dimension inconnue).")
        return self._dimension

    @property
    def is_loaded(self) -> bool:
        return True

    async def initialize(self) -> None:
        if not self._api_key:
            raise RuntimeError("RemoteEmbeddingProvider sans api_key.")
        return None

    async def _post(self, inputs: List[str]) -> List[List[float]]:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx requis pour le provider remote : pip install httpx")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {"model": self._model_id, "input": inputs}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._base_url}/embeddings", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        vecs = [item["embedding"] for item in data["data"]]
        if vecs:
            self._dimension = len(vecs[0])
        return vecs

    async def embed(self, text: str) -> List[float]:
        if not text or not text.strip():
            if self._dimension:
                return [0.0] * self._dimension
            return [0.0] * 1536
        try:
            return (await self._post([text]))[0]
        except Exception as e:
            Logger.error(f"[RemoteEmbeddingProvider] echec : {e}")
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await self._post(texts)

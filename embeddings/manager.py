"""
embeddings/manager.py
=====================
Gestionnaire des fournisseurs d'embedding.
Calque le comportement du ProviderManager.
"""

from typing import Dict, Optional, List, Callable, Awaitable
from utils.logger import Logger
from embeddings.base import EmbeddingProvider


class EmbeddingProviderManager:
    """
    Gère les providers d'embedding.
    - Enregistrement des providers par ID.
    - Sélection du provider actif.
    - Délégation des appels.
    """

    def __init__(self):
        self._providers: Dict[str, EmbeddingProvider] = {}
        self._active_provider_id: Optional[str] = None
        self._emit_func: Optional[Callable[[str, dict], Awaitable[None]]] = None

    def set_emitter(self, emit_func: Callable[[str, dict], Awaitable[None]]) -> None:
        """Permet d'injecter la fonction d'émission d'événements (EventBus)."""
        self._emit_func = emit_func

    def register_provider(self, provider: EmbeddingProvider) -> None:
        """Enregistre un provider."""
        provider_id = provider.model_name
        if provider_id in self._providers:
            Logger.warning(f"[EmbeddingProviderManager] Provider déjà enregistré : {provider_id}")
            return
        self._providers[provider_id] = provider
        Logger.info(f"[EmbeddingProviderManager] Provider enregistré : {provider_id}")

    def get_provider(self, provider_id: str) -> Optional[EmbeddingProvider]:
        """Retourne un provider par son ID."""
        return self._providers.get(provider_id)

    def set_active_provider(self, provider_id: str) -> None:
        """Change le provider actif. Doit être déjà enregistré."""
        if provider_id not in self._providers:
            raise ValueError(f"Provider '{provider_id}' non enregistré.")
        self._active_provider_id = provider_id
        Logger.info(f"[EmbeddingProviderManager] Provider actif : {provider_id}")

    @property
    def active_provider(self) -> Optional[EmbeddingProvider]:
        """Retourne le provider actif. None si aucun n'est défini."""
        if self._active_provider_id is None:
            return None
        return self._providers.get(self._active_provider_id)

    @property
    def active_provider_id(self) -> Optional[str]:
        return self._active_provider_id

    async def embed(self, text: str) -> List[float]:
        """Délègue l'embedding au provider actif."""
        provider = self.active_provider
        if not provider:
            raise RuntimeError("Aucun provider actif. Appelez set_active_provider() d'abord.")
        if not provider.is_loaded:
            await provider.initialize()
        return await provider.embed(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Délègue le batch au provider actif."""
        provider = self.active_provider
        if not provider:
            raise RuntimeError("Aucun provider actif.")
        if not provider.is_loaded:
            await provider.initialize()
        return await provider.embed_batch(texts)

    @property
    def dimension(self) -> int:
        provider = self.active_provider
        if not provider:
            raise RuntimeError("Aucun provider actif.")
        return provider.dimension

    def list_providers(self) -> List[Dict[str, str]]:
        """Retourne la liste des providers enregistrés (pour le front)."""
        return [
            {
                "id": p.model_name,
                "display_name": p.display_name,
                "dimension": p.dimension if p.is_loaded else "unknown",
                "loaded": p.is_loaded
            }
            for p in self._providers.values()
        ]
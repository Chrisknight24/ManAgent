"""
base_provider.py
================
Classe abstraite de base et exceptions de domaine pour tous les providers IA.
Gère nativement le pool de clés API et le cooldown par clé.
"""

from typing import Optional, AsyncGenerator, Type, List, Dict, Any
from pydantic import BaseModel
from abc import ABC, abstractmethod
import time
from core.i18n import _
from utils.logger import Logger


# =========================================================
# EXCEPTIONS DE DOMAINE TYPÉES
# =========================================================

class ProviderError(Exception):
    """Exception de base pour toutes les erreurs des fournisseurs IA."""
    pass

class ProviderQuotaExhaustedError(ProviderError):
    """Levée quand toutes les clés du fournisseur ont atteint leur quota ou rate limit (429)."""
    pass

class ProviderAuthError(ProviderError):
    """Levée en cas d'erreur d'authentification (clé invalide / 401 / 403)."""
    pass

class ProviderServiceUnavailableError(ProviderError):
    """Levée en cas d'indisponibilité du service distant (503 / 500)."""
    pass

class ProviderModelNotFoundError(ProviderError):
    """Levée quand le modèle demandé n'existe pas ou n'est pas accessible avec les clés fournies."""
    pass


# =========================================================
# BASE PROVIDER
# =========================================================

class BaseProvider(ABC):
    """
    Classe abstraite de base pour tous les providers IA.
    Encapsule la gestion native du pool de clés API ordonnées et le cooldown.
    """

    def __init__(self):
        self.system_prompt: str = ""
        self.provider_name: Optional[str] = None
        self.provider_id: Optional[str] = None
        self.model_name: Optional[str] = None

        # Pool de clés ordonnées
        self.api_keys_pool: List[Dict[str, Any]] = []
        self.active_key_index: int = 0
        self._key_cooldowns: Dict[str, float] = {}  # key_str -> timestamp expiration cooldown

    def set_api_keys_pool(self, keys: List[Any]):
        """
        Configure le pool ordonné de clés API pour ce provider.
        Supporte aussi bien une liste de dicts [{id, name, key, priority, is_active}]
        qu'une liste de chaînes simples ou une chaîne unique.
        """
        self.api_keys_pool = []
        self._key_cooldowns.clear()

        if isinstance(keys, str):
            keys = [keys]
        elif not isinstance(keys, list):
            keys = []

        for idx, k in enumerate(keys):
            if isinstance(k, str):
                key_str = k.strip()
                if key_str:
                    self.api_keys_pool.append({
                        "id": f"key_{idx+1}",
                        "name": f"Key {idx+1}",
                        "key": key_str,
                        "priority": idx + 1,
                        "is_active": True
                    })
            elif isinstance(k, dict):
                key_str = k.get("key", k.get("key_value", k.get("keyValue", ""))).strip()
                if key_str:
                    self.api_keys_pool.append({
                        "id": k.get("id", f"key_{idx+1}"),
                        "name": k.get("name", f"Key {idx+1}"),
                        "key": key_str,
                        "priority": k.get("priority", idx + 1),
                        "is_active": k.get("is_active", k.get("isActive", True))
                    })

        # Tri stable par priorité croissante (1 = priorité la plus haute)
        self.api_keys_pool.sort(key=lambda item: item.get("priority", 999))
        self.active_key_index = 0
        Logger.info(f"[{getattr(self, 'provider_id', self.provider_name)}] Configuré avec {len(self.api_keys_pool)} clé(s) API dans le pool.")

    def get_active_api_key(self) -> Optional[str]:
        """
        Retourne la première clé active non en cooldown,
        ou la clé dont le cooldown expire le plus tôt.
        """
        now = time.time()
        for idx, k_info in enumerate(self.api_keys_pool):
            if not k_info.get("is_active", True):
                continue
            key_str = k_info.get("key", "")
            if key_str and now >= self._key_cooldowns.get(key_str, 0):
                self.active_key_index = idx
                return key_str

        # Si toutes les clés sont en cooldown, sélectionner celle qui expire le plus tôt
        if self.api_keys_pool:
            best_key = None
            min_cd = float("inf")
            for idx, k_info in enumerate(self.api_keys_pool):
                if not k_info.get("is_active", True):
                    continue
                key_str = k_info.get("key", "")
                if key_str:
                    cd = self._key_cooldowns.get(key_str, 0)
                    if cd < min_cd:
                        min_cd = cd
                        best_key = key_str
                        self.active_key_index = idx
            return best_key

        return None

    def mark_key_in_cooldown(self, key_str: str, cooldown_seconds: float = 60.0):
        """Met une clé en quarantaine/cooldown après une erreur 429 ou 503."""
        if key_str:
            self._key_cooldowns[key_str] = time.time() + cooldown_seconds
            Logger.warning(f"[{getattr(self, 'provider_id', self.provider_name)}] Clé API mise en cooldown pour {cooldown_seconds:.0f}s suite à saturation.")

    def has_available_keys(self) -> bool:
        """Indique si au moins une clé active n'est pas en cooldown."""
        if not self.api_keys_pool:
            return False
        now = time.time()
        for k_info in self.api_keys_pool:
            if not k_info.get("is_active", True):
                continue
            key_str = k_info.get("key", "")
            if key_str and now >= self._key_cooldowns.get(key_str, 0):
                return True
        return False

    @abstractmethod
    async def initialize(self):
        """Initialise le provider."""
        pass

    @abstractmethod
    async def generate_response(self, user_message: str) -> str:
        """Génère une réponse textuelle complète."""
        pass

    @abstractmethod
    async def stream_response(
        self,
        message: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        """Streaming temps réel tokens IA OU Demande d'outil."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Vérifie si le provider est disponible."""
        pass

    @abstractmethod
    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None,
        media_assets: Optional[list] = None
    ) -> BaseModel:
        """Génère une réponse IA forcée dans un schéma strict Pydantic."""
        pass

    def has_capability(self, capability: str) -> bool:
        """Vérifie si le provider supporte une capacité spécifique."""
        return False

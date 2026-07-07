"""
provider_manager.py
===================
Manager central des providers IA (Version Stateless).
"""

from typing import List, AsyncGenerator, Type, Any
from pydantic import BaseModel
from providers.base_provider import BaseProvider
from utils.logger import Logger
from core.i18n import _
class ProviderManager:

    def __init__(self):
        self.providers: List[BaseProvider] = []

    def register_provider(self, provider: BaseProvider):
        self.providers.append(provider)
        #Logger.info(f"add provider{provider.provider_name}")
        Logger.info(f"Registered provider: {getattr(provider, 'provider_id', provider.provider_name)}")

    def get_provider(self, provider_id: str) -> BaseProvider | None:
        normalized_id = provider_id.lower()
        for p in self.providers:
            if getattr(p, "provider_id", "").lower() == normalized_id:
                return p
        return None

    async def initialize(self):
        Logger.info("Initializing provider manager")
        for provider in self.providers:
            try:
                Logger.info(f"Checking availability for: {getattr(provider, 'provider_id', 'Unknown')}")
                await provider.initialize()
            except Exception as e:
                Logger.error(f"Provider initialization failed: {getattr(provider, 'provider_id', 'Unknown')} -> {str(e)}")

    # =====================================================
    # STREAMING (Texte libre - Chat classique)
    # =====================================================
    async def stream_response(
        self,
        message: str,
        provider_id: str,
        model_id: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        
        provider = self.get_provider(provider_id)
        if not provider:
            raise RuntimeError(_("Le provider '{}' est introuvable.").format(provider_id))

        provider.model_name = model_id
        async for chunk in provider.stream_response(message, context, tools):
            yield chunk

    async def generate_structured_output(
        self,
        prompt: str,
        provider_id: str,
        model_id: str,
        response_schema: Type[BaseModel],
        context: list = None
    ) -> BaseModel:
        """
        Génère une réponse strictement typée selon un schéma Pydantic.
        Retry uniquement sur les erreurs de validation (JSON/Pydantic).
        """
        import asyncio
        from pydantic import ValidationError
        from json import JSONDecodeError

        provider = self.get_provider(provider_id)
        if not provider:
            raise RuntimeError(f"Le provider '{provider_id}' est introuvable.")

        provider.model_name = model_id
        
        max_retries = 3
        current_prompt = prompt

        for attempt in range(max_retries):
            try:
                return await provider.generate_structured_output(current_prompt, response_schema, context)
                
            except (ValidationError, JSONDecodeError) as e:
                # Log de l'échec interne
                Logger.warning(f"[ProviderManager] Erreur de structuration ({provider_id}/{model_id}) - Tentative {attempt + 1}/{max_retries} : {str(e)}")
                
                if attempt == max_retries - 1:
                    # Échec définitif : On remonte une erreur qui pointe l'incapacité du modèle
                    error_msg = (
                        f"Le modèle '{model_id}' a échoué à générer une réponse conforme au schéma {response_schema.__name__} "
                        f"après {max_retries} tentatives. Structure attendue non respectée par le LLM."
                    )
                    Logger.error(f"[ProviderManager] ❌ {error_msg}")
                    
                    # On lève une exception claire qui indique le problème sans artifice
                    raise RuntimeError(_("{error_msg} | Détail technique : {}").format(str(e))) from e
                
                # Feedback de correction technique pour le modèle (instruction système)
                error_feedback = (
                    _("\n\n--- ERREUR DE STRUCTURE (Tentative {}/{}) ---\n").format(attempt + 2, max_retries) +
                    _("Détail : {}\n").format(str(e))+
                    _("Action : Ré-analyse la consigne et fournis uniquement un JSON valide respectant strictement le schéma requis.")
                )
                
                current_prompt = prompt + error_feedback
                await asyncio.sleep(0.5)
                
    def clear(self):
        self.providers.clear()
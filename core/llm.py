"""
llm.py
======
Interface unifiée représentant un moteur cognitif (Le Cerveau).
Encapsule les fournisseurs d'IA de manière "Stateless" pour éviter les conflits asynchrones.
"""

from typing import Type, AsyncGenerator, List, Dict
from pydantic import BaseModel
from providers.provider_manager import ProviderManager
from utils.logger import Logger

class Llm:
    """
    Représente l'intelligence artificielle d'une Entité.
    Maintient son propre contexte de travail sans polluer le Provider partagé.
    """

    def __init__(self, provider_manager: ProviderManager, provider_id: str, model_id: str, system_prompt: str = ""):
        self.provider_manager = provider_manager
        self.provider_id = provider_id
        self.model_id = model_id
        self.system_prompt = system_prompt
        
        # Le contexte cognitif local de ce cerveau
        self.context: List[Dict[str, str]] = []

    def set_system_prompt(self, prompt: str):
        self.system_prompt = prompt

    def set_context(self, context_messages: List[Dict[str, str]]):
        self.context = context_messages

    def add_to_context(self, role: str, content: str):
        self.context.append({"role": role, "content": content})

    def clear_context(self):
        self.context.clear()

    def _build_full_context(self) -> List[Dict[str, str]]:
        """
        Génère un contexte complet en injectant le system_prompt à la volée.
        Cela évite de modifier l'état global du Provider (Thread-safe/Async-safe).
        """
        full_context = []
        if self.system_prompt:
            full_context.append({"role": "system", "content": self.system_prompt})
        full_context.extend(self.context)
        return full_context

    # =====================================================
    # MÉTHODES D'INFÉRENCE (Bridge vers ProviderManager)
    # =====================================================

    async def generate_text(self, prompt: str) -> str:
        """Génération de texte libre en utilisant la mémoire de l'entité."""
        Logger.debug(f"[LLM] Inférence texte demandée ({self.provider_id}/{self.model_id})")
        provider = self.provider_manager.get_provider(self.provider_id)
        if not provider:
            raise RuntimeError(f"Provider {self.provider_id} introuvable.")
        
        # Construction d'un contexte éphémère complet pour la requête
        ephemeral_context = self._build_full_context()
        ephemeral_context.append({"role": "user", "content": prompt})
        
        # Formatage brut en string si le provider ne prend pas de liste (Fallback de sécurité)
        # Idéalement, le BaseProvider devra accepter une liste de dicts à l'avenir.
        text_prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in ephemeral_context])
        
        provider.model_name = self.model_id
        return await provider.generate_response(text_prompt)

    async def generate_structured(self, prompt: str, schema: Type[BaseModel]) -> BaseModel:
        """Génération contrainte pour l'architecture 'Tout est Plan'."""
        Logger.debug(f"[LLM] Inférence structurée demandée : {schema.__name__}")
        
        # On passe le contexte complet contenant le system_prompt
        return await self.provider_manager.generate_structured_output(
            prompt=prompt,
            provider_id=self.provider_id,
            model_id=self.model_id,
            response_schema=schema,
            context=self._build_full_context()
        )

    async def stream(self, prompt: str, tools: list = None) -> AsyncGenerator[str | dict, None]:
        """Génération en flux continu (avec interception d'outils)."""
        Logger.debug(f"[LLM] Inférence stream demandée avec {len(tools) if tools else 0} outils.")
        
        async for chunk in self.provider_manager.stream_response(
            message=prompt,
            provider_id=self.provider_id,
            model_id=self.model_id,
            context=self._build_full_context(),
            tools=tools
        ):
            yield chunk
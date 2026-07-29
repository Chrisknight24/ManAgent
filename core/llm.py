"""
llm.py
======
Interface unifiée représentant un moteur cognitif (Le Cerveau).
Encapsule les fournisseurs d'IA de manière "Stateless" pour éviter les conflits asynchrones.
"""

import time
from typing import Type, AsyncGenerator, List, Dict, Optional
from pydantic import BaseModel
from providers.provider_manager import ProviderManager
from utils.logger import Logger

class Llm:
    """
    Représente l'intelligence artificielle d'une Entité.
    Maintient son propre contexte de travail sans polluer le Provider partagé.
    """

    def __init__(self, provider_manager: ProviderManager, provider_id: str, model_id: str, 
                 system_prompt: str = "", runtime_state=None):
        self.provider_manager = provider_manager
        self.provider_id = provider_id
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.runtime_state = runtime_state  # <--- NOUVEAU
        
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
        full_context = []
        if self.system_prompt:
            full_context.append({"role": "system", "content": self.system_prompt})
        full_context.extend(self.context)
        return full_context

    async def generate_text(self, prompt: str, tag: Optional[str] = None) -> str:
        Logger.debug(f"[LLM] Inférence texte demandée ({self.provider_id}/{self.model_id})")
        provider = self.provider_manager.get_provider(self.provider_id)
        if not provider:
            raise RuntimeError(f"Provider {self.provider_id} introuvable.")
        
        ephemeral_context = self._build_full_context()
        ephemeral_context.append({"role": "user", "content": prompt})
        
        text_prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in ephemeral_context])
        provider.model_name = self.model_id

        # --- Récupération auto du mission_id depuis le contexte ---
        mission_id = None
        if self.runtime_state and hasattr(self.runtime_state, 'execution_context'):
            mission_id = self.runtime_state.execution_context.get("mission_id")

        event_tag = tag or "generate_text"
        started = time.monotonic()
        try:
            response = await provider.generate_response(text_prompt)
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="text",
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=text_prompt,
                response=response,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=True
            )
            return response
        except Exception as e:
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="text",
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=text_prompt,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=False
            )
            raise

    async def generate_structured(self, prompt: str, schema: Type[BaseModel], tag: Optional[str] = None,
                                  mission_id: Optional[str] = None) -> BaseModel:
        Logger.debug(f"[LLM] Inférence structurée demandée : {schema.__name__}")

        # --- Récupération auto du mission_id si non fourni ---
        if mission_id is None and self.runtime_state and hasattr(self.runtime_state, 'execution_context'):
            mission_id = self.runtime_state.execution_context.get("mission_id")

        event_tag = tag or schema.__name__
        full_context = self._build_full_context()
        started = time.monotonic()
        try:
            result = await self.provider_manager.generate_structured_output(
                prompt=prompt,
                provider_id=self.provider_id,
                model_id=self.model_id,
                response_schema=schema,
                context=full_context
            )
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="structured",
                schema=schema.__name__,
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=prompt,
                context=full_context,
                response=result.model_dump(mode='json') if hasattr(result, "model_dump") else str(result),
                duration_ms=int((time.monotonic() - started) * 1000),
                success=True
            )
            return result
        except Exception as e:
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="structured",
                schema=schema.__name__,
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=prompt,
                context=full_context,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=False
            )
            raise

    async def stream(self, prompt: str, tools: list = None, tag: Optional[str] = None) -> AsyncGenerator[str | dict, None]:
        Logger.debug(f"[LLM] Inférence stream demandée avec {len(tools) if tools else 0} outils.")

        mission_id = None
        if self.runtime_state and hasattr(self.runtime_state, 'execution_context'):
            mission_id = self.runtime_state.execution_context.get("mission_id")

        event_tag = tag or "stream"
        started = time.monotonic()
        chunk_count = 0
        try:
            async for chunk in self.provider_manager.stream_response(
                message=prompt,
                provider_id=self.provider_id,
                model_id=self.model_id,
                context=self._build_full_context(),
                tools=tools
            ):
                chunk_count += 1
                yield chunk
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="stream",
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=prompt,
                chunk_count=chunk_count,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=True
            )
        except Exception as e:
            Logger.event(
                "llm_call",
                mission_id=mission_id,
                tag=event_tag,
                kind="stream",
                provider_id=self.provider_id,
                model_id=self.model_id,
                prompt=prompt,
                chunk_count=chunk_count,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
                success=False
            )
            raise
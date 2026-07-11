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

    async def generate_text(self, prompt: str, tag: Optional[str] = None) -> str:
        """
        Génération de texte libre en utilisant la mémoire de l'entité.

        `tag` identifie l'appelant pour l'observabilité (ex: "Presentator_report",
        "Presentator_error"). Si omis, on retombe sur "generate_text" — moins précis,
        mais jamais bloquant : aucun appelant existant n'est cassé par cet ajout.
        """
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

        # --- NOUVEAU : observabilité — un seul point d'instrumentation capture TOUS les
        # appels LLM texte du système, quel que soit l'appelant (Presentator aujourd'hui,
        # potentiellement d'autres entités demain — voir la remarque sur l'Orchestrateur
        # en mode direct, qui lui passe par generate_structured, capté séparément).
        event_tag = tag or "generate_text"
        started = time.monotonic()
        try:
            response = await provider.generate_response(text_prompt)
            Logger.event(
                "llm_call",
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

    async def generate_structured(self, prompt: str, schema: Type[BaseModel], tag: Optional[str] = None) -> BaseModel:
        """
        Génération contrainte pour l'architecture 'Tout est Plan'.

        `tag` identifie l'appelant pour l'observabilité. Si omis, on retombe sur le nom
        du schéma (schema.__name__) — un très bon proxy par défaut ici : "Plan" ==
        Planner, "RerankedLessons" == Advisor, "FeasibilityDecision" == Solver, etc.
        Chaque appelant garde la liberté de passer un tag plus précis plus tard.
        """
        Logger.debug(f"[LLM] Inférence structurée demandée : {schema.__name__}")

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
            # --- Leçon tirée du bug A.1 : asyncio.TimeoutError a un str() vide, ce qui
            # rendait le log "Échec du reranker LLM : " illisible. On capture désormais
            # SYSTÉMATIQUEMENT error_type en plus du message, pour tous les appels.
            Logger.event(
                "llm_call",
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
        """Génération en flux continu (avec interception d'outils)."""
        Logger.debug(f"[LLM] Inférence stream demandée avec {len(tools) if tools else 0} outils.")

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
            # Un seul événement de synthèse à la fin — logguer chaque chunk serait du bruit
            Logger.event(
                "llm_call", tag=event_tag, kind="stream", provider_id=self.provider_id,
                model_id=self.model_id, prompt=prompt, chunk_count=chunk_count,
                duration_ms=int((time.monotonic() - started) * 1000), success=True
            )
        except Exception as e:
            Logger.event(
                "llm_call", tag=event_tag, kind="stream", provider_id=self.provider_id,
                model_id=self.model_id, prompt=prompt, chunk_count=chunk_count,
                error=str(e), error_type=type(e).__name__,
                duration_ms=int((time.monotonic() - started) * 1000), success=False
            )
            raise
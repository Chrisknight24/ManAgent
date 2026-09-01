"""gemini_provider.py
==================

Implémentation Gemini moderne du runtime utilisant le nouveau SDK unifié google-genai.
Supporte nativement le pool multi-clés de BaseProvider sans hack externe.
"""

import os
from typing import Type, List, Optional, Any, AsyncGenerator
from pydantic import BaseModel
import asyncio

from google import genai
from google.genai import types
from google.genai.errors import APIError

from providers.base_provider import (
    BaseProvider,
    ProviderQuotaExhaustedError,
    ProviderAuthError,
    ProviderServiceUnavailableError,
    ProviderError
)
from utils.logger import Logger
from core.i18n import _


class GeminiProvider(BaseProvider):
    """
    Provider Gemini officiel du runtime AutoCUse.
    Résilience multi-clés native basée sur BaseProvider.
    """

    def __init__(self, api_keys: Any, model_name: str, system_prompt: str):
        super().__init__()
        self.provider_name = "gemini"
        self.provider_id = "gemini"
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.client = None
        self.chat_session = None
        self._current_client_key: Optional[str] = None

        # Configuration native du pool de clés
        self.set_api_keys_pool(api_keys)

    def _get_clean_model_id(self) -> str:
        model = self.model_name.strip() if self.model_name else ""
        if not model or model.lower() == "default":
            raise ValueError(_("CRASH PRÉVENTIF : Aucun modèle valide fourni pour Gemini (Reçu: '{}').").format(model))
        return model

    async def _get_or_create_client(self, force_new_key: bool = False) -> genai.Client:
        """Obtient ou instancie le client SDK avec la clé active du pool."""
        active_key = self.get_active_api_key()
        if not active_key:
            raise ProviderQuotaExhaustedError(
                _("[GeminiProvider] Aucune clé API valide ou disponible (toutes en cooldown ou absentes).")
            )

        if self.client is None or force_new_key or self._current_client_key != active_key:
            target_model = self._get_clean_model_id()
            masked_key = active_key[:8] if len(active_key) >= 8 else active_key
            Logger.info(f"[GeminiProvider] Initialisation du client avec clé [{masked_key}...] pour modèle '{target_model}'")
            self.client = genai.Client(api_key=active_key)
            self._current_client_key = active_key

            config = types.GenerateContentConfig()
            if self.system_prompt and self.system_prompt.strip():
                config.system_instruction = self.system_prompt

            self.chat_session = self.client.aio.chats.create(
                model=target_model,
                config=config
            )

        return self.client

    async def initialize(self):
        try:
            await self._get_or_create_client()
            Logger.info("[GeminiProvider] Initialisé avec succès via SDK google-genai.")
        except Exception as e:
            Logger.error(_("Gemini initialization failed: {}").format(str(e)))
            raise

    # =====================================================
    # MOTEUR DE FAILOVER MULTI-CLÉS
    # =====================================================
    async def _execute_with_failover(self, func, *args, **kwargs):
        """
        Exécute une fonction SDK avec rotation transparente des clés en cas de 429/503.
        Lève ProviderQuotaExhaustedError si toutes les clés du pool sont saturées.
        """
        total_keys = max(1, len(self.api_keys_pool))
        attempts = 0

        while attempts < total_keys:
            current_key = self.get_active_api_key()
            if not current_key:
                raise ProviderQuotaExhaustedError(
                    _("[GeminiProvider] 💥 Quota épuisé sur toutes les clés Gemini ({count} clé(s) en cooldown).").format(count=len(self.api_keys_pool))
                )

            try:
                await self._get_or_create_client()
                return await func(*args, **kwargs)

            except APIError as e:
                masked_key = current_key[:8] if len(current_key) >= 8 else current_key
                # 429: Too Many Requests / Resource Exhausted, 503: Service Unavailable
                if e.code in [429, 503] or "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                    attempts += 1
                    self.mark_key_in_cooldown(current_key, cooldown_seconds=60.0)
                    self.client = None
                    self._current_client_key = None

                    Logger.warning(
                        f"[Gemini Failover] Code {e.code} sur clé [{masked_key}...]. "
                        f"Rotation de clé (Essai {attempts}/{total_keys})..."
                    )

                    if self.has_available_keys():
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        Logger.error(_("[Gemini Failover] 💥 Toutes les clés Gemini configurées sont saturées."))
                        raise ProviderQuotaExhaustedError(
                            _("[GeminiProvider] Quota épuisé sur l'ensemble des clés ({count} clé(s)).").format(count=len(self.api_keys_pool))
                        ) from e
                elif e.code in [401, 403]:
                    self.mark_key_in_cooldown(current_key, cooldown_seconds=3600.0)
                    raise ProviderAuthError(_("[GeminiProvider] Clé API invalide ou accès refusé : {}").format(str(e))) from e
                else:
                    raise e
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    attempts += 1
                    self.mark_key_in_cooldown(current_key, cooldown_seconds=60.0)
                    self.client = None
                    self._current_client_key = None
                    if self.has_available_keys():
                        await asyncio.sleep(0.5)
                        continue
                    raise ProviderQuotaExhaustedError(_("[GeminiProvider] Quota épuisé : {}").format(err_str)) from e
                raise e

        raise ProviderQuotaExhaustedError(
            _("[GeminiProvider] 💥 Échec après tentative sur toutes les clés ({count} clé(s)).").format(count=total_keys)
        )

    # =====================================================
    # GÉNÉRATION
    # =====================================================
    async def generate_response(self, user_message: str) -> str:
        async def _run():
            if not self.chat_session:
                await self._get_or_create_client()
            response = await self.chat_session.send_message(user_message)
            return response.text if response.text else ""
            
        return await self._execute_with_failover(_run)

    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None,
        media_assets: Optional[list] = None
    ) -> BaseModel:
        
        async def _run():
            Logger.debug(f"[Gemini] Structured Output request for schema: {response_schema.__name__}")
            client = await self._get_or_create_client()

            contents = []
            if context:
                for msg in context:
                    gemini_role = "model" if msg["role"] == "assistant" else "user"
                    contents.append(
                        types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
                    )
            
            parts = [types.Part.from_text(text=prompt)]
            if media_assets:
                for asset in media_assets:
                    if hasattr(asset, "filepath") and asset.filepath and os.path.exists(asset.filepath):
                        try:
                            with open(asset.filepath, "rb") as f:
                                img_data = f.read()
                            import mimetypes
                            mime_type, _encoding = mimetypes.guess_type(asset.filepath)
                            if not mime_type:
                                if asset.filepath.lower().endswith(".png"):
                                    mime_type = "image/png"
                                elif asset.filepath.lower().endswith((".jpg", ".jpeg")):
                                    mime_type = "image/jpeg"
                                elif asset.filepath.lower().endswith(".webp"):
                                    mime_type = "image/webp"
                                else:
                                    mime_type = "image/png"
                            parts.append(
                                types.Part.from_bytes(
                                    data=img_data,
                                    mime_type=mime_type
                                )
                            )
                            Logger.info(f"[GeminiProvider] Image attachée avec succès : {asset.filename} ({mime_type})")
                        except Exception as ex:
                            Logger.error(f"[GeminiProvider] Erreur lors du chargement de l'image {asset.filename} : {ex}")

            contents.append(types.Content(role="user", parts=parts))

            if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                schema_dict = response_schema.model_json_schema()
            else:
                schema_dict = response_schema

            def remove_additional_props(obj):
                if isinstance(obj, dict):
                    obj.pop("additionalProperties", None)
                    for v in obj.values():
                        remove_additional_props(v)
                elif isinstance(obj, list):
                    for item in obj:
                        remove_additional_props(item)

            remove_additional_props(schema_dict)

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema_dict
            )
            if self.system_prompt and self.system_prompt.strip():
                config.system_instruction = self.system_prompt

            response = await client.aio.models.generate_content(
                model=self._get_clean_model_id(),
                contents=contents,
                config=config
            )

            return response_schema.model_validate_json(response.text)

        return await self._execute_with_failover(_run)
    
    # =====================================================
    # STREAM RESPONSE (AVEC FUNCTION CALLING)
    # =====================================================
    async def stream_response(
        self,
        user_message: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        
        total_keys = max(1, len(self.api_keys_pool))
        attempts = 0

        while attempts < total_keys:
            current_key = self.get_active_api_key()
            if not current_key:
                raise ProviderQuotaExhaustedError(
                    _("[GeminiProvider] Quota épuisé sur toutes les clés pour le streaming.")
                )

            try:
                client = await self._get_or_create_client()

                contents = []
                if context:
                    for msg in context:
                        gemini_role = "model" if msg["role"] == "assistant" else "user"
                        contents.append(
                            types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
                        )

                contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
                )

                gemini_tools = None
                if tools:
                    declarations = []
                    for t in tools:
                        declarations.append(
                            types.FunctionDeclaration(
                                name=t.get("name"),
                                description=t.get("description"),
                                parameters=t.get("parameters")
                            )
                        )
                    gemini_tools = [types.Tool(function_declarations=declarations)]

                config = types.GenerateContentConfig(tools=gemini_tools)
                if self.system_prompt and self.system_prompt.strip():
                    config.system_instruction = self.system_prompt

                response_stream = await client.aio.models.generate_content_stream(
                    model=self._get_clean_model_id(),
                    contents=contents,
                    config=config
                )

                async for chunk in response_stream:
                    if chunk.function_calls:
                        for fc in chunk.function_calls:
                            args = dict(fc.args) if fc.args else {}
                            Logger.info(f"[Gemini] Tool call intercepted: {fc.name}")
                            yield {"call": fc.name, "args": args}
                        return 

                    if chunk.text:
                        yield chunk.text

                return

            except APIError as e:
                if e.code in [429, 503] or "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                    attempts += 1
                    self.mark_key_in_cooldown(current_key, 60.0)
                    self.client = None
                    self._current_client_key = None
                    if self.has_available_keys():
                        await asyncio.sleep(0.5)
                        continue
                    raise ProviderQuotaExhaustedError(_("[GeminiProvider] Quota épuisé streaming.")) from e
                raise e
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    attempts += 1
                    self.mark_key_in_cooldown(current_key, 60.0)
                    self.client = None
                    self._current_client_key = None
                    if self.has_available_keys():
                        await asyncio.sleep(0.5)
                        continue
                    raise ProviderQuotaExhaustedError(_("[GeminiProvider] Quota épuisé streaming.")) from e
                raise e

    async def is_available(self) -> bool:
        return self.has_available_keys()

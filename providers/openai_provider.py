"""
openai_provider.py
==================

Provider OpenAI officiel du runtime.
Supporte nativement le pool multi-clés ordonné avec failover automatique.
"""

from typing import Type, AsyncGenerator, Optional, Any, List
from pydantic import BaseModel
import aiohttp
import json
import asyncio
from core.i18n import _
from providers.base_provider import (
    BaseProvider,
    ProviderQuotaExhaustedError,
    ProviderAuthError,
    ProviderServiceUnavailableError,
    ProviderError
)
from utils.logger import Logger


class OpenAIProvider(BaseProvider):

    def __init__(
        self,
        api_keys: Any,
        model_name: str,
        system_prompt: str
    ):
        super().__init__()
        self.provider_name = "openai"
        self.provider_id = "openai"
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.history = []
        self.set_api_keys_pool(api_keys)

    async def initialize(self):
        Logger.info(f"Initializing OpenAI provider: {self.model_name}")

    async def _execute_http_request(self, payload: dict, endpoint: str = "https://api.openai.com/v1/chat/completions") -> dict:
        """Exécute une requête HTTP avec rotation multi-clés sur 429 / 503."""
        total_keys = max(1, len(self.api_keys_pool))
        attempts = 0

        while attempts < total_keys:
            active_key = self.get_active_api_key()
            if not active_key:
                raise ProviderQuotaExhaustedError(_("[OpenAIProvider] Aucune clé API active ou disponible."))

            headers = {
                "Authorization": f"Bearer {active_key}",
                "Content-Type": "application/json"
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(endpoint, headers=headers, json=payload) as response:
                        if response.status == 200:
                            return await response.json()

                        error_text = await response.text()
                        masked_key = active_key[:8] if len(active_key) >= 8 else active_key

                        if response.status in [429, 503] or "rate_limit" in error_text.lower() or "quota" in error_text.lower():
                            attempts += 1
                            self.mark_key_in_cooldown(active_key, 60.0)
                            Logger.warning(f"[OpenAI Failover] Erreur {response.status} sur clé [{masked_key}...]. Rotation ({attempts}/{total_keys}).")
                            if self.has_available_keys():
                                await asyncio.sleep(0.5)
                                continue
                            raise ProviderQuotaExhaustedError(_("[OpenAIProvider] Quota épuisé sur toutes les clés : {}").format(error_text))
                        elif response.status in [401, 403]:
                            self.mark_key_in_cooldown(active_key, 3600.0)
                            raise ProviderAuthError(_("[OpenAIProvider] Clé invalide (401/403) : {}").format(error_text))
                        else:
                            raise ProviderError(_("OpenAI API error {}: {}").format(response.status, error_text))

            except aiohttp.ClientError as ce:
                attempts += 1
                self.mark_key_in_cooldown(active_key, 30.0)
                if self.has_available_keys():
                    await asyncio.sleep(0.5)
                    continue
                raise ProviderServiceUnavailableError(_("OpenAI network error: {}").format(str(ce))) from ce

        raise ProviderQuotaExhaustedError(_("[OpenAIProvider] Quota épuisé sur toutes les clés."))

    async def generate_response(self, user_message: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": self.model_name,
            "messages": messages
        }

        data = await self._execute_http_request(payload)
        return data["choices"][0]["message"]["content"]

    async def is_available(self) -> bool:
        return self.has_available_keys()

    async def stream_response(
        self,
        user_message: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        total_keys = max(1, len(self.api_keys_pool))
        attempts = 0

        while attempts < total_keys:
            active_key = self.get_active_api_key()
            if not active_key:
                raise ProviderQuotaExhaustedError(_("[OpenAIProvider] Aucune clé disponible pour le streaming."))

            messages = [{"role": "system", "content": self.system_prompt}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": user_message})

            payload = {
                "model": self.model_name,
                "messages": messages,
                "stream": True
            }
            if tools:
                payload["tools"] = [{"type": "function", "function": t} for t in tools]

            headers = {
                "Authorization": f"Bearer {active_key}",
                "Content-Type": "application/json"
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload) as response:
                        if response.status in [429, 503]:
                            error_text = await response.text()
                            attempts += 1
                            self.mark_key_in_cooldown(active_key, 60.0)
                            if self.has_available_keys():
                                await asyncio.sleep(0.5)
                                continue
                            raise ProviderQuotaExhaustedError(_("[OpenAIProvider] Quota streaming épuisé : {}").format(error_text))

                        if response.status != 200:
                            error_text = await response.text()
                            raise ProviderError(f"OpenAI streaming error: {error_text}")

                        is_tool_call = False
                        tool_name = ""
                        tool_args_str = ""

                        async for raw_line in response.content:
                            line = raw_line.decode("utf-8").strip()
                            if not line or not line.startswith("data:"):
                                continue

                            line = line[5:].strip()
                            if line == "[DONE]":
                                break

                            try:
                                data = json.loads(line)
                                delta = data["choices"][0]["delta"]

                                if "tool_calls" in delta and delta["tool_calls"]:
                                    is_tool_call = True
                                    tc = delta["tool_calls"][0]
                                    if "function" in tc:
                                        if "name" in tc["function"]:
                                            tool_name += tc["function"]["name"]
                                        if "arguments" in tc["function"]:
                                            tool_args_str += tc["function"]["arguments"]
                                    continue

                                chunk = delta.get("content", "")
                                if chunk and not is_tool_call:
                                    yield chunk
                            except Exception:
                                continue

                        if is_tool_call:
                            Logger.info(f"[OpenAI] Tool call intercepted: {tool_name}")
                            try:
                                parsed_args = json.loads(tool_args_str) if tool_args_str else {}
                            except json.JSONDecodeError:
                                Logger.error(f"OpenAI invalid JSON for tool args: {tool_args_str}")
                                parsed_args = {}
                            yield {"call": tool_name, "args": parsed_args}

                return

            except aiohttp.ClientError as ce:
                attempts += 1
                self.mark_key_in_cooldown(active_key, 30.0)
                if self.has_available_keys():
                    await asyncio.sleep(0.5)
                    continue
                raise ProviderServiceUnavailableError(f"OpenAI stream network error: {ce}") from ce

    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None,
        media_assets: Optional[list] = None
    ) -> BaseModel:
        Logger.debug(f"[OpenAI] Structured Output request for schema: {response_schema.__name__}")
        schema_dict = response_schema.model_json_schema()
        strict_system_prompt = (
            f"{self.system_prompt}\n\n"
            "CRITICAL INSTRUCTION: You MUST respond ONLY with a valid JSON object.\n"
            "The JSON must strictly adhere to the following schema:\n"
            f"{json.dumps(schema_dict, indent=2)}"
        )

        messages = [{"role": "system", "content": strict_system_prompt}]
        if context:
            messages.extend(context)

        if media_assets:
            import base64
            import os
            import mimetypes
            user_content = [{"type": "text", "text": prompt}]
            for asset in media_assets:
                if hasattr(asset, "filepath") and asset.filepath and os.path.exists(asset.filepath):
                    try:
                        with open(asset.filepath, "rb") as f:
                            img_data = f.read()
                        base64_str = base64.b64encode(img_data).decode("utf-8")
                        mime_type, _ = mimetypes.guess_type(asset.filepath)
                        if not mime_type:
                            mime_type = "image/png"
                        user_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_str}"}
                        })
                    except Exception as ex:
                        Logger.error(f"[OpenAIProvider] Erreur image : {ex}")
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "stream": False
        }

        data = await self._execute_http_request(payload)
        raw_json_str = data["choices"][0]["message"]["content"]

        def clean_none(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "tool_args" and v is None:
                        obj[k] = {}
                    else:
                        clean_none(v)
            elif isinstance(obj, list):
                for item in obj:
                    clean_none(item)
            return obj

        parsed_data = json.loads(raw_json_str)
        cleaned_data = clean_none(parsed_data)
        return response_schema.model_validate_json(json.dumps(cleaned_data))

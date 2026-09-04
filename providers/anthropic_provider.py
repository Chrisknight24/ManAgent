"""
anthropic_provider.py
=====================

Provider Anthropic (Claude) officiel du runtime.
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


class AnthropicProvider(BaseProvider):

    def __init__(
        self,
        api_keys: Any,
        model_name: str,
        system_prompt: str
    ):
        super().__init__()
        self.provider_name = "claude"
        self.provider_id = "claude"
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.history = []
        self.set_api_keys_pool(api_keys)

    async def initialize(self):
        Logger.info(f"Initializing Anthropic provider: {self.model_name}")

    def _prepare_messages_and_system(self, user_message: str, context: list = None):
        system_parts = [self.system_prompt]
        messages = []
        raw_messages = []
        if context:
            raw_messages.extend(context)

        for msg in raw_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            else:
                anth_role = "user" if role == "user" else "assistant"
                messages.append({"role": anth_role, "content": content})

        messages.append({"role": "user", "content": user_message})
        system_prompt_merged = "\n\n".join([p for p in system_parts if p])

        consolidated = []
        for msg in messages:
            if consolidated and consolidated[-1]["role"] == msg["role"]:
                consolidated[-1]["content"] += "\n\n" + msg["content"]
            else:
                consolidated.append(msg)

        return consolidated, system_prompt_merged

    async def _execute_http_request(self, payload: dict, endpoint: str = "https://api.anthropic.com/v1/messages") -> dict:
        total_keys = max(1, len(self.api_keys_pool))
        attempts = 0

        while attempts < total_keys:
            self.check_cancelled()
            active_key = self.get_active_api_key()
            if not active_key:
                raise ProviderQuotaExhaustedError(_("[AnthropicProvider] Aucune clé API active ou disponible."))

            headers = {
                "x-api-key": active_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(endpoint, headers=headers, json=payload) as response:
                        if response.status == 200:
                            res_data = await response.json()
                            self.promote_key(active_key)
                            return res_data

                        error_text = await response.text()
                        masked_key = active_key[:8] if len(active_key) >= 8 else active_key

                        if response.status in [429, 529, 503] or "rate_limit" in error_text.lower() or "overloaded" in error_text.lower():
                            attempts += 1
                            self.mark_key_in_cooldown(active_key, 60.0)
                            Logger.warning(f"[Anthropic Failover] Erreur {response.status} sur clé [{masked_key}...]. Rotation ({attempts}/{total_keys}).")
                            if self.has_available_keys():
                                await asyncio.sleep(0.5)
                                continue
                            raise ProviderQuotaExhaustedError(_("[AnthropicProvider] Quota épuisé sur toutes les clés : {}").format(error_text))
                        elif response.status in [401, 403]:
                            self.mark_key_in_cooldown(active_key, 3600.0)
                            raise ProviderAuthError(_("[AnthropicProvider] Clé invalide (401/403) : {}").format(error_text))
                        else:
                            raise ProviderError(_("Anthropic API error {}: {}").format(response.status, error_text))

            except aiohttp.ClientError as ce:
                attempts += 1
                self.mark_key_in_cooldown(active_key, 30.0)
                if self.has_available_keys():
                    await asyncio.sleep(0.5)
                    continue
                raise ProviderServiceUnavailableError(_("Anthropic network error: {}").format(str(ce))) from ce

        raise ProviderQuotaExhaustedError(_("[AnthropicProvider] Quota épuisé sur toutes les clés."))

    async def generate_response(self, user_message: str) -> str:
        messages, system_prompt = self._prepare_messages_and_system(user_message)
        payload = {
            "model": self.model_name or "claude-3-5-sonnet-20241022",
            "messages": messages,
            "max_tokens": 4096,
            "system": system_prompt
        }
        data = await self._execute_http_request(payload)
        for item in data.get("content", []):
            if item.get("type") == "text":
                return item.get("text", "")
        return ""

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
            self.check_cancelled()
            active_key = self.get_active_api_key()
            if not active_key:
                raise ProviderQuotaExhaustedError(_("[AnthropicProvider] Aucune clé disponible pour le streaming."))

            messages, system_prompt = self._prepare_messages_and_system(user_message, context)
            payload = {
                "model": self.model_name or "claude-3-5-sonnet-20241022",
                "messages": messages,
                "max_tokens": 4096,
                "system": system_prompt,
                "stream": True
            }

            if tools:
                anth_tools = []
                for t in tools:
                    anth_tools.append({
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "input_schema": t.get("parameters", {"type": "object", "properties": {}})
                    })
                payload["tools"] = anth_tools

            headers = {
                "x-api-key": active_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload) as response:
                        if response.status in [429, 529, 503]:
                            error_text = await response.text()
                            attempts += 1
                            self.mark_key_in_cooldown(active_key, 60.0)
                            if self.has_available_keys():
                                await asyncio.sleep(0.5)
                                continue
                            raise ProviderQuotaExhaustedError(_("[AnthropicProvider] Quota streaming épuisé : {}").format(error_text))

                        if response.status != 200:
                            error_text = await response.text()
                            raise ProviderError(f"Anthropic streaming error: {error_text}")

                        is_tool_call = False
                        tool_name = ""
                        tool_args_str = ""

                        async for raw_line in response.content:
                            line = raw_line.decode("utf-8").strip()
                            if not line or not line.startswith("data:"):
                                continue

                            line = line[5:].strip()
                            try:
                                data = json.loads(line)
                                evt_type = data.get("type")

                                if evt_type == "content_block_start":
                                    cb = data.get("content_block", {})
                                    if cb.get("type") == "tool_use":
                                        is_tool_call = True
                                        tool_name = cb.get("name", "")
                                elif evt_type == "content_block_delta":
                                    delta = data.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield delta.get("text", "")
                                    elif delta.get("type") == "input_json_delta":
                                        tool_args_str += delta.get("partial_json", "")
                            except Exception:
                                continue

                        if is_tool_call:
                            Logger.info(f"[Anthropic] Tool call intercepted: {tool_name}")
                            try:
                                parsed_args = json.loads(tool_args_str) if tool_args_str else {}
                            except json.JSONDecodeError:
                                parsed_args = {}
                            yield {"call": tool_name, "args": parsed_args}

                return

            except aiohttp.ClientError as ce:
                attempts += 1
                self.mark_key_in_cooldown(active_key, 30.0)
                if self.has_available_keys():
                    await asyncio.sleep(0.5)
                    continue
                raise ProviderServiceUnavailableError(f"Anthropic stream network error: {ce}") from ce

    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None,
        media_assets: Optional[list] = None
    ) -> BaseModel:
        schema_dict = response_schema.model_json_schema()
        tool_spec = {
            "name": "record_response",
            "description": "Enregistre la réponse structurée finale.",
            "input_schema": schema_dict
        }

        messages, system_prompt = self._prepare_messages_and_system(prompt, context)
        payload = {
            "model": self.model_name or "claude-3-5-sonnet-20241022",
            "messages": messages,
            "max_tokens": 4096,
            "system": system_prompt,
            "tools": [tool_spec],
            "tool_choice": {"type": "tool", "name": "record_response"}
        }

        data = await self._execute_http_request(payload)
        for item in data.get("content", []):
            if item.get("type") == "tool_use" and item.get("name") == "record_response":
                return response_schema.model_validate(item.get("input", {}))

        raise ProviderError("Anthropic did not return the expected structured output tool call.")

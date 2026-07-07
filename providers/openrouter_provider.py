"""
openrouter_provider.py
=====================

Provider OpenRouter (agrégateur multi-LLM) du runtime.
API compatible OpenAI, utilise aiohttp comme les autres.
"""

from typing import Type, AsyncGenerator
from pydantic import BaseModel
import aiohttp
import json

from providers.base_provider import BaseProvider
from utils.logger import Logger

from core.i18n import _

class OpenRouterProvider(BaseProvider):

    # =====================================================
    # CONSTRUCTOR
    # =====================================================
    def __init__(
        self,
        api_key: str,
        model_name: str,
        system_prompt: str
    ):
        super().__init__()

        self.provider_name = "openrouter"
        self.model_name = model_name
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.history = []

    # =====================================================
    # INITIALIZE
    # =====================================================
    async def initialize(self):
        Logger.info(
            f"Initializing OpenRouter provider with model: {self.model_name}"
        )

    # =====================================================
    # GENERATE RESPONSE (BLOC COMPLET)
    # =====================================================
    async def generate_response(
        self,
        user_message: str
    ) -> str:
        try:
            Logger.debug("Sending request to OpenRouter (Block)")

            messages = [{"role": "system", "content": self.system_prompt}]
            messages.extend(self.history)
            messages.append({"role": "user", "content": user_message})

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model_name,
                        "messages": messages
                    }
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("OpenRouter API error: {}").format(error_text))

                    data = await response.json()

            final_text = data["choices"][0]["message"]["content"]
            Logger.debug("OpenRouter response received")
            return final_text

        except Exception as e:
            Logger.error(_("OpenRouter generation error: {}").format(str(e)))
            raise

    # =====================================================
    # PROVIDER STATUS
    # =====================================================
    async def is_available(self) -> bool:
        return True

    # =====================================================
    # STREAM RESPONSE (AVEC INTERCEPTION DE TOOLS)
    # =====================================================
    async def stream_response(
        self,
        user_message: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        try:
            Logger.debug("Starting OpenRouter stateless streaming with Tools")

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

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("OpenRouter streaming error: {}").format(error_text))

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
                        Logger.info(f"[OpenRouter] Tool call intercepted: {tool_name}")
                        try:
                            parsed_args = json.loads(tool_args_str) if tool_args_str else {}
                        except json.JSONDecodeError:
                            Logger.error(_("OpenRouter invalid JSON for tool args: {}").format(tool_args_str))
                            parsed_args = {}

                        yield {"call": tool_name, "args": parsed_args}

            Logger.debug("OpenRouter streaming sequence completed")

        except Exception as e:
            Logger.error(_("OpenRouter stream error: {}").format(str(e)))
            raise

    # =====================================================
    # STRUCTURED OUTPUT (POUR L'ARCHITECTURE DU SOLVER)
    # =====================================================
    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None
    ) -> BaseModel:
        try:
            Logger.debug(f"[OpenRouter] Structured Output request for schema: {response_schema.__name__}")

            schema_dict = response_schema.model_json_schema()
            strict_system_prompt = (
                f"{self.system_prompt}\n\n"
                f"CRITICAL INSTRUCTION: You MUST respond ONLY with a valid JSON object.\n"
                f"The JSON must strictly adhere to the following schema:\n"
                f"{json.dumps(schema_dict, indent=2)}"
            )

            messages = [{"role": "system", "content": strict_system_prompt}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model_name,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "stream": False
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("OpenRouter structured output error: {}").format(error_text))
                    data = await response.json()

            raw_json_str = data["choices"][0]["message"]["content"]

            # Nettoyage des None (identique aux autres providers)
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

        except Exception as e:
            Logger.error(_("OpenRouter structured generation error: {}").format(str(e)))
            raise
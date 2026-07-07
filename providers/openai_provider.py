"""
openai_provider.py
==================

Provider OpenAI officiel du runtime.
S'appuie sur des requêtes HTTP asynchrones (aiohttp) pour coller
parfaitement à la philosophie légère et sans SDK rigide du runtime.
"""

from typing import Type, AsyncGenerator
from pydantic import BaseModel
import aiohttp
import json
from core.i18n import _
# Provider abstrait de base
from providers.base_provider import BaseProvider

# Logger runtime
from utils.logger import Logger
from core.i18n import _

class OpenAIProvider(BaseProvider):

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

        self.provider_name = "openai"
        self.model_name = model_name
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.history = []

    # =====================================================
    # INITIALIZE
    # =====================================================
    async def initialize(self):
        Logger.info(
            f"Initializing OpenAI provider: {self.model_name}"
        )

    # =====================================================
    # GENERATE RESPONSE (BLOC COMPLET)
    # =====================================================
    async def generate_response(
        self,
        user_message: str
    ) -> str:
        try:
            Logger.debug("Sending request to OpenAI (Block)")

            # Construction des messages
            messages = [{"role": "system", "content": self.system_prompt}]
            messages.extend(self.history)
            messages.append({"role": "user", "content": user_message})

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
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
                        raise RuntimeError(f"OpenAI API error: {error_text}")

                    data = await response.json()

            final_text = data["choices"][0]["message"]["content"]
            Logger.debug("OpenAI response received")
            return final_text

        except Exception as e:
            Logger.error(_("OpenAI generation error: {}").format(str(e)))
            raise

    # =====================================================
    # PROVIDER STATUS
    # =====================================================
    async def is_available(self) -> bool:
        # On considère le service disponible si l'initialisation s'est faite sans crash
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
            Logger.debug("Starting OpenAI stateless streaming with Tools capability")

            # 1. Préparation du contexte
            messages = [{"role": "system", "content": self.system_prompt}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": user_message})

            # 2. Préparation du Payload
            payload = {
                "model": self.model_name,
                "messages": messages,
                "stream": True
            }

            if tools:
                payload["tools"] = [{"type": "function", "function": t} for t in tools]

            # 3. Requête streaming HTTP
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(f"OpenAI streaming error: {error_text}")

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

                            # INTERCEPTION DU FUNCTION CALLING
                            if "tool_calls" in delta and delta["tool_calls"]:
                                is_tool_call = True
                                tc = delta["tool_calls"][0]
                                
                                if "function" in tc:
                                    if "name" in tc["function"]:
                                        tool_name += tc["function"]["name"]
                                    if "arguments" in tc["function"]:
                                        tool_args_str += tc["function"]["arguments"]
                                continue

                            # STREAMING TEXTE STANDARD
                            chunk = delta.get("content", "")
                            if chunk and not is_tool_call:
                                yield chunk

                        except Exception:
                            continue

                    # RESOLUTION FINALE DE L'OUTIL
                    if is_tool_call:
                        Logger.info(f"[OpenAI] Tool call intercepted: {tool_name}")
                        try:
                            parsed_args = json.loads(tool_args_str) if tool_args_str else {}
                        except json.JSONDecodeError:
                            Logger.error(f"OpenAI hallucinated invalid JSON for tool args: {tool_args_str}")
                            parsed_args = {}

                        yield {"call": tool_name, "args": parsed_args}

            Logger.debug("OpenAI streaming sequence completed")

        except Exception as e:
            Logger.error(_("OpenAI stream error: {}").format(str(e)))
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
            Logger.debug(f"[OpenAI] Structured Output request for schema: {response_schema.__name__}")

            schema_dict = response_schema.model_json_schema()
            strict_system_prompt = (
                _("{self.system_prompt}\n\n") +
                _("CRITICAL INSTRUCTION: You MUST respond ONLY with a valid JSON object.\n") +
                _(f"The JSON must strictly adhere to the following schema:\n") +
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
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("OpenAI structured output error: {}").format(error_text))
                    data = await response.json()

            raw_json_str = data["choices"][0]["message"]["content"]

            # Harnais de nettoyage des None (identique à ton fix sur Groq)
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
            Logger.error(_("OpenAI structured generation error: {}").format(str(e)))
            raise
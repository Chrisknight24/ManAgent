"""
groq_provider.py
================

Provider Groq du runtime.

IMPORTANT :
-------------
Groq expose une API compatible OpenAI.

Donc :
nous utilisons simplement des requêtes HTTP.

Contrairement au SDK Gemini :
nous pouvons ici utiliser du vrai async.
"""


# =========================================================
# IMPORTS
# =========================================================
from typing import Type
from pydantic import BaseModel
import aiohttp


# Provider abstrait
#
from providers.base_provider import BaseProvider


# Logger runtime
#
from utils.logger import Logger

from typing import AsyncGenerator
import json
from core.i18n import _
# =========================================================
# GROQ PROVIDER
# =========================================================

class GroqProvider(BaseProvider):


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

        self.provider_name = "groq"

        self.model_name = model_name

        self.api_key = api_key

        self.system_prompt = system_prompt
        self.history = []


    # =====================================================
    # INITIALIZE
    # =====================================================

    async def initialize(self):

        Logger.info(
            f"Initializing Groq provider: {self.model_name}"
        )


    # =====================================================
    # GENERATE RESPONSE
    # =====================================================

    async def generate_response(
        self,
        user_message: str
    ) -> str:

        try:

            Logger.debug(
                "Sending request to Groq"
            )

            # =============================================
            # Construction historique messages
            # =============================================

            messages = [

                {
                    "role": "system",
                    "content": self.system_prompt
                }

            ]

            # Ajout historique
            #
            messages.extend(
                self.history
            )

            # Ajout message user actuel
            #
            messages.append(
                {
                    "role": "user",
                    "content": user_message
                }
            )

            # =============================================
            # Appel HTTP async
            # =============================================

            async with aiohttp.ClientSession() as session:

                async with session.post(

                    "https://api.groq.com/openai/v1/chat/completions",

                    headers={
                        "Authorization":
                            f"Bearer {self.api_key}",

                        "Content-Type":
                            "application/json"
                    },

                    json={
                        "model": self.model_name,
                        "messages": messages
                    }

                ) as response:

                    # =====================================
                    # Vérification erreurs HTTP
                    # =====================================

                    if response.status != 200:

                        error_text = await response.text()

                        raise RuntimeError(
                            _("Groq API error: {}").format(error_text)
                        )

                    # =====================================
                    # Lecture JSON réponse
                    # =====================================

                    data = await response.json()

            # =============================================
            # Extraction réponse finale
            # =============================================

            final_text = (
                data["choices"][0]
                ["message"]
                ["content"]
            )

            # =============================================
            # Sauvegarde historique
            # =============================================

            # self.history.append(
            #     {
            #         "role": "user",
            #         "content": user_message
            #     }
            # )

            # self.history.append(
            #     {
            #         "role": "assistant",
            #         "content": final_text
            #     }
            # )

            Logger.debug(
                "Groq response received"
            )

            return final_text

        except Exception as e:

            Logger.error(
                _("Groq generation error: {}").format(str(e))
            )

            raise

    # =====================================================
    # PROVIDER STATUS
    # =====================================================

    async def is_available(self) -> bool:

        return True
    
    # =====================================================
    # STREAM RESPONSE (AVEC FUNCTION CALLING)
    # =====================================================

    async def stream_response(
        self,
        user_message: str,
        context: list = None,
        tools: list = None  # NOUVEAU
    ) -> AsyncGenerator[str | dict, None]:
        
        try:
            Logger.debug("Starting Groq stateless streaming with Tools capability")

            # 1. Prompt Système & Contexte
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

            # Si des outils sont disponibles, on les formate au standard OpenAI
            if tools:
                payload["tools"] = [{"type": "function", "function": t} for t in tools]

            # 3. Requête streaming
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("Groq streaming error: {}").format(error_text))

                    # =====================================
                    # ACCUMULATEURS DE FUNCTION CALL
                    # =====================================
                    is_tool_call = False
                    tool_name = ""
                    tool_args_str = ""

                    # Lecture stream réseau
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

                            # INTERCEPTION DU TOOL CALL
                            if "tool_calls" in delta and delta["tool_calls"]:
                                is_tool_call = True
                                tc = delta["tool_calls"][0]
                                
                                if "function" in tc:
                                    if "name" in tc["function"]:
                                        tool_name += tc["function"]["name"]
                                    if "arguments" in tc["function"]:
                                        tool_args_str += tc["function"]["arguments"]
                                continue

                            # STREAMING TEXTE CLASSIQUE
                            chunk = delta.get("content", "")
                            if chunk and not is_tool_call:
                                yield chunk

                        except Exception:
                            continue

                    # =====================================
                    # RÉSOLUTION FINALE DU TOOL CALL
                    # =====================================
                    if is_tool_call:
                        Logger.info(f"[Groq] Tool call intercepted: {tool_name}")
                        try:
                            # Tentative de parsing sécurisé des arguments
                            parsed_args = json.loads(tool_args_str) if tool_args_str else {}
                        except json.JSONDecodeError:
                            Logger.error(_("Groq hallucinated invalid JSON for tool args: {}").format(tool_args_str))
                            parsed_args = {} # Harnais de sécurité

                        yield {"call": tool_name, "args": parsed_args}

            Logger.debug("Groq streaming sequence completed")

        except Exception as e:
            Logger.error(_("Groq stream error: {}").format(str(e)))
            raise

    
    # =====================================================
    # STRUCTURED OUTPUT (JSON / PYDANTIC)
    # =====================================================
    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None
    ) -> BaseModel:
        try:
            Logger.debug(f"[Groq] Structured Output request for schema: {response_schema.__name__}")

            schema_dict = response_schema.model_json_schema()
            strict_system_prompt = (
                f"{self.system_prompt}\n\n"+
                _("CRITICAL INSTRUCTION: You MUST respond ONLY with a valid JSON object.\n")+
                _("The JSON must strictly adhere to the following schema:\n")+
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
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(_("Groq structured output error: {}").format(error_text))
                    data = await response.json()

            raw_json_str = data["choices"][0]["message"]["content"]

            # ---- NOUVEAU : Nettoyage des None dans tool_args ----
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
            # ---------------------------------------------------

            return response_schema.model_validate_json(json.dumps(cleaned_data))

        except Exception as e:
            #TODO tout nest pas forcemet une erreru de generation
            Logger.error(_("generation error: {}").format(str(e)))
            raise
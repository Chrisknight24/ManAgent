"""gemini_provider.py
==================

Implémentation Gemini moderne du runtime utilisant le nouveau SDK unifié google-genai.

IMPORTANT :
-------------
Ce fichier encapsule TOUTE la logique Gemini.
Le reste du runtime ne doit PAS connaître :
    - google.genai
    - API Gemini
    - détails SDK
    """

import os
import json
from typing import Type, List, Optional
from pydantic import BaseModel
import asyncio
from typing import AsyncGenerator

from google import genai
from google.genai import types
from google.genai.errors import APIError

from providers.base_provider import BaseProvider
from utils.logger import Logger
from core.i18n import _

class GeminiProvider(BaseProvider):
    """
    Provider Gemini officiel du runtime AutoCUse.
    Prise en charge native de l'API asynchrone et du compte gratuit.
    """

    def __init__(self, api_key: str, model_name: str, system_prompt: str):
        super().__init__()
        self.provider_name = "gemini"
        self.model_name = model_name
        self.original_api_key = api_key # La clé de prod/config initiale
        self.current_api_key = api_key  # La clé actuellement utilisée
        self.system_prompt = system_prompt

        self.client = None
        self.chat_session = None
        
        # Chargement du pool de failover depuis un fichier externe
        self._dev_keys_pool = self._load_keys_from_file()
        self._current_key_index = 0

    def _load_keys_from_file(self) -> List[str]:
        """
        Charge les clés Gemini depuis un fichier JSON externe.
        Le fichier est cherché dans l'ordre :
         1. Variable d'environnement GEMINI_KEYS_FILE
         2. ~/.autocuse/gemini_keys.json
         3. keys.json dans le répertoire du script
        Si aucun fichier n'est trouvé, retourne une liste vide (pas de failover).
        """
        possible_paths = []
        # 1. Variable d'environnement
        env_path = os.environ.get("GEMINI_KEYS_FILE")
        if env_path:
            possible_paths.append(env_path)
        # 2. Dossier utilisateur
        possible_paths.append(os.path.expanduser("~/.autocuse/gemini_keys.json"))
        # 3. Répertoire du script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths.append(os.path.join(script_dir, "keys.json"))

        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        data = json.load(f)
                        keys = data.get("gemini_keys", [])
                        if keys and isinstance(keys, list):
                            Logger.info(f"[GeminiProvider] Chargé {len(keys)} clés de secours depuis {path}")
                            return keys
                except Exception as e:
                    Logger.warning(f"[GeminiProvider] Erreur lors du chargement du fichier de clés {path}: {e}")
        Logger.info("[GeminiProvider] Aucun fichier de clés de secours trouvé. Failover désactivé.")
        return []

    def _get_clean_model_id(self) -> str:
        model = self.model_name.strip() if self.model_name else ""
        if not model or model.lower() == "default":
            raise ValueError(_("CRASH PRÉVENTIF : Aucun modèle valide fourni pour Gemini (Reçu: '{}').").format(model))
        return model

    async def initialize(self):
        try:
            target_model = self._get_clean_model_id()
            Logger.info(f"Initializing modern Gemini provider with model: {target_model}")

            # Instanciation avec la clé courante (Failover ou Prod)
            self.client = genai.Client(api_key=self.current_api_key)

            config = types.GenerateContentConfig()
            if self.system_prompt and self.system_prompt.strip():
                config.system_instruction = self.system_prompt

            self.chat_session = self.client.aio.chats.create(
                model=target_model,
                config=config
            )
            Logger.info("Gemini provider initialized successfully with new SDK Client.")

        except Exception as e:
            Logger.error(_("Gemini initialization failed: {}").format(str(e)))
            raise

    # =====================================================
    # MOTEUR DE FAILOVER (basé sur pool de clés externes)
    # =====================================================
    async def _execute_with_failover(self, func, *args, **kwargs):
        """
        Exécute une fonction asynchrone du SDK. 
        Si une erreur 429 ou 503 survient, pivote sur la clé suivante et retente.
        """
        attempts = 0
        # On utilise le pool de clés + la clé originale comme dernière chance
        pool = self._dev_keys_pool.copy()
        # On ajoute la clé originale en fin de liste (ou en début ?)
        # Mettons-la en première position pour éviter de tourner inutilement
        # On va plutôt utiliser la clé courante initialement, puis les secours
        # Le pool inclura les secours, et on commence avec la clé originale.
        # On va créer une liste de clés à essayer : d'abord la clé originale (si elle n'est pas déjà dans le pool), puis les secours
        # Pour éviter les doublons, on met la clé originale en premier si elle n'est pas déjà dans le pool.
        if self.original_api_key not in pool:
            all_keys = [self.original_api_key] + pool
        else:
            all_keys = pool  # si la clé originale est dans le pool, on l'utilise une fois
        max_attempts = len(all_keys)

        while attempts < max_attempts:
            try:
                # Si le client est tombé, on le relance
                if not self.client:
                    await self.initialize()
                    
                # Exécution de la fonction passée en paramètre
                return await func(*args, **kwargs)

            except APIError as e:
                # Code 429: Too Many Requests (Rate limit), Code 503: Service Unavailable
                if e.code in [429, 503]:
                    attempts += 1
                    Logger.warning(f"[Gemini Failover] Erreur {e.code} rencontrée. Tentative de rotation de clé (Essai {attempts}/{max_attempts})...")
                    
                    if attempts < max_attempts:
                        # Rotation de clé : on prend la clé suivante dans la liste all_keys
                        self.current_api_key = all_keys[attempts]
                        Logger.info("[Gemini Failover] Rotation réussie. Réinitialisation du client.")
                        self.client = None # Forcera la réinitialisation à la prochaine boucle
                        await asyncio.sleep(1) # Petite pause pour laisser respirer le réseau
                        continue
                    else:
                        Logger.error(_("[Gemini Failover] 💥 Toutes les clés de secours ont été épuisées."))
                        raise e # On a épuisé nos jokers, on remonte l'erreur
                else:
                    # Ce n'est pas une erreur de quota (ex: erreur 400 Bad Request), on ne failover pas
                    raise e
            except Exception as e:
                 # Toute autre exception non liée à l'API de base
                 raise e

    # =====================================================
    # MÉTHODES ENCAPSULÉES PAR LE FAILOVER
    # =====================================================

    async def generate_response(self, user_message: str) -> str:
        async def _run():
            if not self.chat_session:
                await self.initialize()
            response = await self.chat_session.send_message(user_message)
            return response.text if response.text else ""
            
        return await self._execute_with_failover(_run)


    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None
    ) -> BaseModel:
        
        async def _run():
            Logger.debug(f"[Gemini] Structured Output request for schema: {response_schema.__name__}")

            contents = []
            if context:
                for msg in context:
                    gemini_role = "model" if msg["role"] == "assistant" else "user"
                    contents.append(
                        types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
                    )
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))

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

            response = await self.client.aio.models.generate_content(
                model=self._get_clean_model_id(),
                contents=contents,
                config=config
            )

            return response_schema.model_validate_json(response.text)

        # On encapsule tout le bloc d'exécution via le gestionnaire de failover
        return await self._execute_with_failover(_run)
    
    #=====================================================
    # STREAM RESPONSE (AVEC FUNCTION CALLING)
    # =====================================================

    async def stream_response(
        self,
        user_message: str,
        context: list = None,
        tools: list = None  # NOUVEAU
    ) -> AsyncGenerator[str | dict, None]:
        
        try:
            Logger.debug("Starting native Gemini stateless streaming with Tools capability")

            if not self.client:
                await self.initialize()

            # 1. Traduction du contexte
            contents = []
            if context:
                for msg in context:
                    # Note: Si le msg vient d'un appel d'outil précédent, il faudra ajuster le rôle plus tard.
                    # Pour l'instant, on gère l'historique texte standard.
                    gemini_role = "model" if msg["role"] == "assistant" else "user"
                    contents.append(
                        types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
                    )

            # 2. Ajout du message actuel
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
            )

            # 3. Préparation des Outils (Le Harness procédural)
            gemini_tools = None
            if tools:
                declarations = []
                for t in tools:
                    # On convertit le standard OpenAPI vers le format Google GenAI
                    declarations.append(
                        types.FunctionDeclaration(
                            name=t.get("name"),
                            description=t.get("description"),
                            parameters=t.get("parameters")
                        )
                    )
                gemini_tools = [types.Tool(function_declarations=declarations)]

            # 4. Configuration
            config = types.GenerateContentConfig(
                tools=gemini_tools
            )
            if self.system_prompt and self.system_prompt.strip():
                config.system_instruction = self.system_prompt

            # 5. Génération pure
            response_stream = await self.client.aio.models.generate_content_stream(
                model=self._get_clean_model_id(),
                contents=contents,
                config=config
            )

            async for chunk in response_stream:
                
                # INTERCEPTION : Si Gemini décide d'appeler un outil
                if chunk.function_calls:
                    for fc in chunk.function_calls:
                        # Le SDK Google parse déjà les arguments en dict
                        args = dict(fc.args) if fc.args else {}
                        Logger.info(f"[Gemini] Tool call intercepted: {fc.name}")
                        
                        # On crache un dictionnaire et on stoppe le stream
                        yield {"call": fc.name, "args": args}
                    return 

                # STREAMING CLASSIQUE : S'il génère du texte
                if chunk.text:
                    yield chunk.text

            Logger.debug("Gemini streaming sequence completed normally")

        except Exception as e:
            if "429" in str(e):
                Logger.warning("[Gemini] Resource exhausted (Quota limit reached).")
            Logger.error(f"Gemini stream error: {str(e)}")
            raise
        

    # =====================================================
    # PROVIDER STATUS
    # =====================================================

    async def is_available(self) -> bool:
        """
        Vérification de l'état d'activation pour le coordinateur d'état.
        """
        try:
            return (
                self.client is not None and 
                self.chat_session is not None
            )
        except Exception:
            return False
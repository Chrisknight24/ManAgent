"""
base_provider.py
================

Ce fichier définit la classe de base
de TOUS les providers IA du runtime.

IMPORTANT :
-------------
Un "provider" = une source de modèle IA.

Exemples :
-----------
- Gemini
- OpenAI
- Groq
- DeepSeek
- Claude
- Ollama
- LM Studio

Pourquoi cette abstraction est importante ?
--------------------------------------------
Parce que le runtime ne doit PAS dépendre
d'un provider spécifique.

Le runtime doit pouvoir faire :

    provider.generate_response(...)

sans savoir si derrière :
    - Gemini
    - OpenAI
    - Ollama
    - etc.

C'est une architecture fondamentale
dans les systèmes IA modernes.
"""


# =========================================================
# IMPORTS
# =========================================================

# Ajoute ces imports en haut
from typing import Optional, AsyncGenerator, Type
from pydantic import BaseModel

# ABC :
# -----
# "Abstract Base Class"
#
# Sert à créer des classes abstraites.
#
# Une classe abstraite :
#     - ne doit pas être instanciée directement
#     - sert de contrat/interface
#
from abc import ABC


# abstractmethod :
# ----------------
# Permet de forcer les classes enfants
# à implémenter certaines fonctions.
#
from abc import abstractmethod


# typing :
# --------
# Optional :
#     peut être une valeur OU None
#
from typing import Optional
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from core.i18n import _
# =========================================================
# BASE PROVIDER
# =========================================================

class BaseProvider(ABC):
    """
    Classe abstraite de base
    pour tous les providers IA.

    IMPORTANT :
    -------------
    Tous les providers devront hériter
    de cette classe.

    Exemple :
    ----------
    class GeminiProvider(BaseProvider)

    Pourquoi ?
    -----------
    Pour garantir que tous les providers
    exposent la même interface.
    """


    # =====================================================
    # CONSTRUCTOR
    # =====================================================

    def __init__(self):

        """
        Informations communes
        à tous les providers.
        """

        self.system_prompt = ""
        # Nom humain du provider
        #
        # Exemple :
        #   "gemini"
        #   "groq"
        #
        self.provider_name: Optional[str] = None

        # Nom du modèle utilisé
        #
        # Exemple :
        #   "gemini-2.5-pro"
        #   "llama-3-70b"
        #
        self.model_name: Optional[str] = None


    # =====================================================
    # INITIALIZE
    # =====================================================

    @abstractmethod
    async def initialize(self):
        """
        Initialise le provider.

        Exemples :
        ----------
        - charger clé API
        - créer client SDK
        - ouvrir connexion
        - charger modèle local

        IMPORTANT :
        -------------
        Tous les providers DOIVENT
        implémenter cette fonction.
        """

        pass


    # =====================================================
    # GENERATE RESPONSE
    # =====================================================

    @abstractmethod
    async def generate_response(
        self,
        user_message: str
    ) -> str:
        """
        Génère une réponse IA.

        Paramètres :
        -------------
        user_message :
            prompt utilisateur.

        Retour :
        --------
        string contenant la réponse finale.

        IMPORTANT :
        -------------
        Tous les providers devront supporter
        cette fonction.
        """

        pass

    # =====================================================
    # STREAM RESPONSE
    # =====================================================

    @abstractmethod
    async def stream_response(
        self,
        message: str,
        context: list = None,
        tools: list = None  # NOUVEAU : L'injection du catalogue d'outils
    ) -> AsyncGenerator[str | dict, None]:
        """
        Streaming temps réel tokens IA OU Demande d'outil.

        Paramètres :
        -------------
        message : prompt utilisateur
        context : liste standardisée de l'historique
        tools   : Schema OpenAPI des outils autorisés

        Retour :
        --------
        Génère soit des morceaux de texte (str), 
        soit un dictionnaire dict décrivant l'outil à appeler :
        {"call": "nom_outil", "args": {...}}
        """
        pass
    

    # =====================================================
    # PROVIDER STATUS
    # =====================================================

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Vérifie si le provider est disponible.

        Retour :
        --------
        True  -> disponible
        False -> indisponible

        Exemple :
        ----------
        - API key valide
        - serveur accessible
        - modèle chargé
        """

        pass

    @abstractmethod
    async def generate_structured_output(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        context: list = None
    ) -> BaseModel:
        """
        Génère une réponse IA forcée dans un schéma de données strict (JSON/Pydantic).
        Indispensable pour l'architecture "Tout est Plan" du Solver.
        """
        pass
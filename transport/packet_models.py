"""
packet_models.py
================

Ce fichier définit TOUS les formats de packets JSON
utilisés dans le runtime agentique.

IMPORTANT :
-------------
Un "packet" = un message structuré échangé
entre :
    - le client (Qt, app externe, etc.)
    - le runtime Python

Exemple :
----------
Client Qt  --->  Runtime Python

{
    "id": "msg_001",
    "type": "request",
    "action": "chat.send",
    "payload": {
        "content": "Bonjour"
    }
}

Pourquoi ce fichier est IMPORTANT ?
-----------------------------------
Parce qu'il définit le "langage officiel"
du runtime.

Tous les modules dépendront de ces structures :
    - transport
    - orchestrator
    - planner
    - tools
    - memory
    - providers
    - etc.

Donc :
si les packets sont propres,
tout le système devient propre.
"""


# =========================================================
# IMPORTS
# =========================================================

# Optional :
# ----------
# Permet d'indiquer qu'une valeur peut être :
#     - un type donné
#     - OU None
#
# Exemple :
# id: Optional[str]
#
# signifie :
#     soit une string
#     soit None
#
from typing import Optional


# Literal :
# ----------
# Permet de limiter une variable
# à des valeurs précises.
#
# Exemple :
# type: Literal["request"]
#
# signifie :
# type DOIT être exactement "request"
#
from typing import Literal


# Dict :
# -------
# Représente un dictionnaire Python.
#
# Exemple :
# {
#     "content": "bonjour"
# }
#
from typing import Dict


# Any :
# ------
# Signifie :
# "n'importe quel type"
#
# Très utile pour les payloads dynamiques.
#
from typing import Any


# BaseModel :
# ------------
# Classe principale de Pydantic.
#
# Pydantic est une librairie ultra importante
# dans les systèmes Python modernes.
#
# Elle sert à :
#     - valider les données
#     - parser automatiquement le JSON
#     - vérifier les types
#     - générer des erreurs propres
#
# Exemple :
#
# packet = RequestPacket(...)
#
# Pydantic vérifiera automatiquement :
#     - les champs manquants
#     - les mauvais types
#     - les erreurs de structure
#
from pydantic import BaseModel


# =========================================================
# BASE PACKET
# =========================================================

class BasePacket(BaseModel):
    """
    Classe de base pour TOUS les packets.

    Toutes les autres classes hériteront de celle-ci.

    Héritage :
    ----------
    RequestPacket(BasePacket)
    ResponsePacket(BasePacket)
    etc.

    Champs :
    --------
    id :
        identifiant unique optionnel du packet.

        Exemple :
            "msg_001"

        Très utile plus tard pour :
            - tracking
            - réponses async
            - observabilité
            - corrélation request/response

    type :
        type principal du packet.

        Exemple :
            "request"
            "response"
            "event"
            "error"
    """

    id: Optional[str] = None

    type: str


# =========================================================
# REQUEST PACKET
# =========================================================

class RequestPacket(BasePacket):
    """
    Packet envoyé AU runtime.

    Exemple :
    ----------
    {
        "id":"msg_001",
        "type":"request",
        "action":"chat.send",
        "payload":{
            "content":"Bonjour"
        }
    }

    action :
    --------
    Action demandée au runtime.

    Exemple :
        "chat.send"
        "system.reset"
        "tool.execute"

    payload :
    ----------
    "charge utile" du packet.

    Contient les vraies données.
    """

    # Literal force la valeur exacte.
    # Donc :
    # type DOIT être "request"
    #
    type: Literal["request"]

    # Nom de l'action demandée
    #
    action: str

    # Dictionnaire contenant les données utiles
    #
    payload: Dict[str, Any] = {}


# =========================================================
# RESPONSE PACKET
# =========================================================

class ResponsePacket(BasePacket):
    """
    Réponse officielle du runtime.

    Exemple :
    ----------
    {
        "id":"msg_001",
        "type":"response",
        "status":"success",
        "payload":{
            "content":"Salut"
        }
    }
    """

    type: Literal["response"]

    # Indique si l'action a réussi ou échoué
    #
    status: Literal["success", "error"]

    payload: Dict[str, Any] = {}


# =========================================================
# EVENT PACKET
# =========================================================

class EventPacket(BasePacket):
    """
    Packet événementiel.

    IMPORTANT :
    -------------
    Les events sont très importants
    dans les architectures modernes.

    Exemple :
    ----------
    {
        "type":"event",
        "event":"agent.thinking"
    }

    Plus tard :
    -----------
    On pourra avoir :
        - tool.started
        - tool.finished
        - agent.thinking
        - memory.updated
        - planner.created_plan
    """

    type: Literal["event"]

    # Nom de l'événement
    #
    event: str

    payload: Dict[str, Any] = {}


# =========================================================
# ERROR PACKET
# =========================================================

class ErrorPacket(BasePacket):
    """
    Packet d'erreur standardisé.

    Exemple :
    ----------
    {
        "type":"error",
        "message":"Provider unavailable"
    }

    Pourquoi standardiser les erreurs ?
    -----------------------------------
    Parce que plus tard :
        - UI
        - logs
        - observability
        - debugger

    pourront comprendre les erreurs
    automatiquement.
    """

    type: Literal["error"]

    message: str
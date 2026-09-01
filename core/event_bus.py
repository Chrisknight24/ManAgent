"""
event_bus.py
============

Système central d’événements du runtime.

===========================================================
RÔLE
===========================================================

L’EventBus permet de :
    - publier des événements
    - écouter des événements
    - découpler les composants

Exemple :
---------
Orchestrator → emit("thinking.started")
Transport    → écoute et envoie UI
Logger       → écoute pour debug

===========================================================
POURQUOI C’EST IMPORTANT
===========================================================

Sans EventBus :
    tout est couplé (mauvaise architecture)

Avec EventBus :
    système modulaire type agent moderne
"""


# =========================================================
# IMPORTS
# =========================================================

from typing import Callable, Dict, List, Any
from utils.logger import Logger
import asyncio

# =========================================================
# EVENT BUS
# =========================================================

class EventBus:
    """
    Bus d’événements simple mais scalable.
    """


    def __init__(self):
        """
        Stockage des listeners.

        Structure :
        -----------
        {
            "event_name": [callback1, callback2]
        }
        """

        self.listeners: Dict[str, List[Callable]] = {}

    # =====================================================
    # SUBSCRIBE
    # =====================================================

    def subscribe(
        self,
        event_name: str,
        callback: Callable[[Dict[str, Any]], None]
    ):
        """
        Ajoute un listener à un event.

        Exemple :
        ---------
        bus.subscribe("thinking.started", handler)
        """

        if event_name not in self.listeners:
            self.listeners[event_name] = []

        self.listeners[event_name].append(callback)

    # =====================================================
    # EMIT EVENT
    # =====================================================

    async def emit(
        self,
        event_name: str,
        payload: Dict[str, Any] | None = None
    ):
        """
        Émet un événement async.

        Tous les listeners abonnés
        recevront l’événement.
        """

        if payload is None:
            payload = {}

        # =============================================
        # Listeners spécifiques
        # =============================================

        listeners = []

        listeners.extend(
            self.listeners.get(
                event_name,
                []
            )
        )

        # =============================================
        # Wildcard listeners
        # =============================================

        listeners.extend(
            self.listeners.get(
                "*",
                []
            )
        )

        # =============================================
        # Dispatch async
        # =============================================

        for callback in listeners:

            try:

                # Callback async
                #
                if asyncio.iscoroutinefunction(
                    callback
                ):

                    await callback(
                        event_name,
                        payload
                    )

                # Callback sync
                #
                else:

                    callback(
                        event_name,
                        payload
                    )

            except Exception as e:

                Logger.error(
                    f"EventBus listener error: {e}"
                )

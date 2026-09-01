"""
event_forwarder.py
==================

Bridge officiel :

    EventBus
        ↓
    Transport
        ↓
    Frontend UI

IMPORTANT :
-------------
Le runtime interne émet des événements.

Le frontend Qt ne connaît PAS EventBus.

Donc :
ce composant traduit :
    événements internes
vers
    packets JSON stdout.
"""


# =========================================================
# IMPORTS
# =========================================================

from core.event_bus import EventBus

from transport.stdin_transport import (
    StdinTransport
)

from transport.packet_models import (
    EventPacket
)

from utils.logger import Logger


# =========================================================
# EVENT FORWARDER
# =========================================================

class EventForwarder:


    # =====================================================
    # CONSTRUCTOR
    # =====================================================

    def __init__(
        self,
        event_bus: EventBus,
        transport: StdinTransport
    ):

        self.event_bus = event_bus

        self.transport = transport


    # =====================================================
    # START
    # =====================================================

    def start(self):
        """
        Abonne le transport
        à TOUS les événements runtime.
        """

        # =============================================
        # WILDCARD SUBSCRIPTION
        # =============================================
        #
        # "*" signifie :
        # écouter TOUS les événements.
        #
        self.event_bus.subscribe(
            "*",
            self._forward_event
        )

        Logger.info(
            "EventForwarder started"
        )


    # =====================================================
    # FORWARD EVENT
    # =====================================================

    async def _forward_event(
        self,
        event_name: str,
        payload: dict
    ):
        """
        Transforme un événement runtime
        en packet JSON protocolaire.
        """

        try:

            packet = EventPacket(

                type="event",

                event=event_name,

                payload=payload
            )

            await self.transport.send_packet(
                packet
            )

        except Exception as e:

            Logger.error(
                f"Event forwarding error: {e}"
            )

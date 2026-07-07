"""
stdin_transport.py
==================

Transport stdin/stdout du runtime agentique.

===========================================================
PRINCIPE ARCHITECTURAL
===========================================================

Ce module est une couche BAS NIVEAU.

Il ne doit JAMAIS contenir :
    - logique IA
    - planner
    - memory
    - tools
    - business logic

Il fait uniquement :
    - INPUT  : stdin -> JSON -> Packet (Pydantic)
    - OUTPUT : Packet -> JSON -> stdout

===========================================================
POURQUOI C’EST IMPORTANT
===========================================================

Cette séparation permet de remplacer stdin/stdout par :
    - WebSocket
    - gRPC
    - TCP
    - HTTP
    - MCP transport

SANS modifier le runtime interne.

C’est la base des systèmes agentiques modernes.
"""


# =========================================================
# IMPORTS
# =========================================================

import sys
import json
import asyncio
from typing import Union, Optional


# =========================================================
# PACKETS (PROTOCOLE)
# =========================================================

from transport.packet_models import (
    RequestPacket,
    ResponsePacket,
    EventPacket,
    ErrorPacket
)


# =========================================================
# TRANSPORT CLASS
# =========================================================

PacketType = Union[
    RequestPacket,
    ResponsePacket,
    EventPacket,
    ErrorPacket
]


class StdinTransport:
    """
    Transport runtime basé sur stdin/stdout.

    RESPONSABILITÉS :
    ------------------
    - lire stdin (async-safe)
    - parser JSON
    - valider via Pydantic
    - envoyer stdout proprement
    """


    # =====================================================
    # READ PACKET
    # =====================================================

    async def read_packet(self) -> Optional[PacketType]:
        """
        Lit un packet depuis stdin.

        IMPORTANT :
        ------------
        stdin est BLOQUANT.
        On utilise asyncio.to_thread()
        pour ne pas bloquer l’event loop.
        """

        try:

            raw_line = await asyncio.to_thread(
                sys.stdin.readline
            )

            # EOF (process fermé)
            if not raw_line:
                return None

            raw_line = raw_line.strip()

            if not raw_line:
                return None

            # =========================
            # JSON parsing sécurisé
            # =========================

            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                return ErrorPacket(
                    type="error",
                    message="Invalid JSON format"
                )

            if not isinstance(data, dict):
                return ErrorPacket(
                    type="error",
                    message="Packet must be a JSON object"
                )

            packet_type = data.get("type")

            # =========================
            # ROUTING PACKET TYPE
            # =========================

            if packet_type == "request":
                return RequestPacket(**data)

            if packet_type == "response":
                return ResponsePacket(**data)

            if packet_type == "event":
                return EventPacket(**data)

            if packet_type == "error":
                return ErrorPacket(**data)

            # =========================
            # TYPE UNKNOWN
            # =========================

            return ErrorPacket(
                type="error",
                message=f"Unknown packet type: {packet_type}"
            )

        except Exception as e:

            return ErrorPacket(
                type="error",
                message=f"Transport read error: {str(e)}"
            )


    # =====================================================
    # WRITE PACKET
    # =====================================================

    async def send_packet(
        self,
        packet: PacketType
    ) -> None:
        """
        Envoie un packet vers stdout.

        IMPORTANT :
        ------------
        stdout = canal unique de communication.
        Aucun print debug ici.
        """

        try:

            # =========================
            # Pydantic -> dict
            # =========================

            if hasattr(packet, "model_dump"):
                data = packet.model_dump()
            else:
                data = packet

            # =========================
            # dict -> JSON
            # =========================

            json_string = json.dumps(
                data,
                ensure_ascii=False
            )

            # =========================
            # SEND OUTPUT
            # =========================

            print(json_string, flush=True)

        except Exception as e:

            # fallback safe error
            error_packet = ErrorPacket(
                type="error",
                message=f"Transport send error: {str(e)}"
            )

            print(
                json.dumps(error_packet.model_dump()),
                flush=True
            )
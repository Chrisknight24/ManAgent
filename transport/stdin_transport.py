"""
transport/stdin_transport.py
=============================
Transport stdin/stdout asynchrone pour la communication IPC JSON entre ManAgent et l'hôte Qt.
"""

import sys
import json
import asyncio
from typing import Optional, Any, Union, Dict

from transport.packet_models import (
    RequestPacket,
    ResponsePacket,
    ErrorPacket,
    EventPacket,
    CheckpointReachedEvent,
    BreakoutOccurredEvent,
    ExecutionCompletedEvent,
)
from utils.logger import Logger


class StdinTransport:
    """Gère la lecture et l'écriture asynchrones de paquets JSON sur stdin/stdout."""

    def __init__(self):
        self.loop = None

    def _get_loop(self):
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.get_event_loop()
        return self.loop

    async def read_packet(self) -> Optional[Union[RequestPacket, ErrorPacket, Dict[str, Any]]]:
        """Lit une ligne depuis stdin de manière asynchrone et désérialise le paquet JSON."""
        loop = self._get_loop()
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return None
            line_str = line.strip()
            if not line_str:
                return await self.read_packet()

            raw = json.loads(line_str)
            if isinstance(raw, dict):
                action = raw.get("action")
                payload = raw.get("payload", {})
                if action:
                    return RequestPacket(action=action, payload=payload)
                return raw
            return raw
        except json.JSONDecodeError as e:
            Logger.error(f"[StdinTransport] Erreur de désérialisation JSON sur stdin: {e}")
            return ErrorPacket(message=f"JSON invalide: {e}")
        except Exception as e:
            Logger.error(f"[StdinTransport] Erreur de lecture sur stdin: {e}")
            return None

    async def send_packet(self, packet: Any) -> None:
        """Série le paquet en JSON et l'écrit sur stdout suivi d'un saut de ligne."""
        try:
            if hasattr(packet, "dict"):
                data = packet.dict()
            elif hasattr(packet, "__dict__"):
                data = vars(packet)
            elif isinstance(packet, dict):
                data = packet
            else:
                data = {"data": str(packet)}

            output_str = json.dumps(data, ensure_ascii=False)
            sys.stdout.write(output_str + "\n")
            sys.stdout.flush()
        except Exception as e:
            Logger.error(f"[StdinTransport] Erreur d'envoi sur stdout: {e}")

"""
main.py
=======
Point d’entrée principal du runtime (Hub & Spoke Edition).
"""
import asyncio
from core.constants import Actions, Events
from transport.stdin_transport import StdinTransport
from transport.packet_models import RequestPacket, ErrorPacket
from core.orchestrator import Orchestrator
from core.event_bus import EventBus
from core.event_forwarder import EventForwarder
from core.runtime_state import RuntimeState
from providers.provider_manager import ProviderManager
from utils.logger import Logger
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
async def main():
    Logger.info("Starting Universal Agent Runtime (Hub & Spoke Edition)")

    runtime_state = RuntimeState()
    transport = StdinTransport()
    event_bus = EventBus()
    
    event_forwarder = EventForwarder(event_bus, transport)
    event_forwarder.start()

    provider_manager = ProviderManager()

    # Instanciation de l'entreprise agentique
    orchestrator = Orchestrator(provider_manager, event_bus, runtime_state)

    Logger.info("Runtime initialized with Planner and Executor.")
    await transport.send_packet({"type": "event", "event": Events.RUNTIME_READY, "payload": {}})

    while True:
        try:
            packet = await transport.read_packet()
            if packet is None:
                Logger.warning("EOF received")
                break
            if isinstance(packet, ErrorPacket):
                await transport.send_packet(packet)
                continue
            if not isinstance(packet, RequestPacket):
                continue

            # Délégation non-bloquante au PDG
            async def process_packet_task(req_packet):
                try:
                    response = await orchestrator.handle_request(req_packet)
                    if response is not None: await transport.send_packet(response)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    Logger.error(f"Task execution error: {e}")
                    await transport.send_packet({"type": "error", "message": str(e)})

            asyncio.create_task(process_packet_task(packet))

        except Exception as e:
            Logger.error(f"Main loop error: {e}")
            await transport.send_packet({"type": "error", "message": str(e)})

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        Logger.warning("Runtime interrupted")
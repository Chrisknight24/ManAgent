"""Hote de test minimal pour les surfaces TargetRegistry (S6).

Le simulateur utilise les vrais gestionnaires de ManAgent :
- `ToolsManager` pour le manifeste et les contrats ;
- `TargetRegistry` pour les cibles ;
- les methodes de production de `Orchestrator` pour le dispatch et les
  paquets `tool.result`.

Il ne remplace pas un hote externe (CondiFlow) : il sert de banc d'essai
protocole, sans LLM ni interface graphique.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from core.constants import Actions, Events
from core.orchestrator import Orchestrator
from core.target_ingress import apply_target_updates, project_target_payload
from core.targets import TargetRegistry
from tools.tools_manager import ToolsManager
from transport.packet_models import RequestPacket


class _Runtime(SimpleNamespace):
    """Etat runtime minimal, avec la purge officielle des cibles."""

    def __init__(self) -> None:
        super().__init__(
            host_manifest=None,
            target_registry=TargetRegistry(),
            tools_manager=None,
            orchestrator=None,
            language="fr",
        )

    def reset_target_registry(self) -> None:
        self.target_registry.purge(all=True)


class _SimulatedOrchestrator:
    """Orchestrateur sans LLM : dispatch, events et paquets reels."""

    handle_request = Orchestrator.handle_request
    execute_tool = Orchestrator.execute_tool
    _handle_tool_result = Orchestrator._handle_tool_result
    _resolve_target_arguments = Orchestrator._resolve_target_arguments
    _mark_dispatched = staticmethod(Orchestrator._mark_dispatched)
    _execute_external_tool = Orchestrator._execute_external_tool

    def __init__(self, host: "HostSim") -> None:
        self.host = host
        self.runtime_state = host.runtime_state
        self.pending_tool_calls: Dict[str, Any] = {}

    async def propagate_event(self, event_name: str, payload: dict) -> None:
        await self.host._on_event(event_name, payload)


class HostSim:
    """Hote minimal qui repond aux `tool.requested` par un `tool.result`."""

    def __init__(
        self,
        results: Optional[Dict[str, Any]] = None,
        *,
        always_reject: bool = False,
    ) -> None:
        self.runtime_state = _Runtime()
        self.tools_manager = ToolsManager(runtime_state=self.runtime_state)
        self.runtime_state.tools_manager = self.tools_manager
        self.orchestrator = _SimulatedOrchestrator(self)
        self.runtime_state.orchestrator = self.orchestrator

        self.results = copy.deepcopy(results or {})
        self.always_reject = always_reject
        self.events: List[tuple[str, dict]] = []
        self.host_calls: List[dict] = []
        self.manifest_responses: List[Any] = []
        self.last_result: Any = None
        self.last_target_count = 0
        self.last_updates_applied = 0

    async def register_manifest(self, manifest: dict) -> Any:
        """Enregistre un manifeste via le vrai handler de paquet."""
        response = await self.orchestrator.handle_request(
            RequestPacket(
                action=Actions.HOST_MANIFEST_REGISTER,
                payload={"host_manifest": manifest},
            )
        )
        self.manifest_responses.append(response)
        return response

    async def dispatch(self, tool_name: str, arguments: dict) -> Any:
        """Execute un outil hote, puis projette son resultat comme l'executor."""
        raw = await self.orchestrator.execute_tool(tool_name, dict(arguments))
        parsed = self._parse(raw)
        self.last_target_count = 0
        self.last_updates_applied = 0
        if isinstance(parsed, dict):
            contract = self.tools_manager.get_target_contract(tool_name)
            if contract is not None:
                parsed, self.last_target_count = project_target_payload(
                    self.runtime_state.target_registry,
                    contract,
                    parsed,
                    register=True,
                )
                parsed, self.last_updates_applied = apply_target_updates(
                    self.runtime_state.target_registry,
                    parsed,
                )
        self.last_result = parsed
        return parsed

    def events_named(self, name: str) -> List[dict]:
        return [payload for event_name, payload in self.events if event_name == name]

    async def _on_event(self, event_name: str, payload: dict) -> None:
        self.events.append((event_name, copy.deepcopy(payload)))
        if event_name != Events.TOOL_REQUESTED:
            return

        call_id = payload.get("call_id")
        tool_name = payload.get("tool_name", "")
        arguments = payload.get("arguments") or {}
        result = self._next_result(tool_name, arguments)
        self.host_calls.append({
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": copy.deepcopy(arguments),
            "arguments_display": copy.deepcopy(payload.get("arguments_display")),
            "result": copy.deepcopy(result),
        })
        await self.orchestrator.handle_request(
            RequestPacket(
                action=Actions.TOOL_RESULT,
                payload={
                    "call_id": call_id,
                    "result": json.dumps(result, ensure_ascii=False),
                },
            )
        )

    def _next_result(self, tool_name: str, arguments: dict) -> dict:
        if self.always_reject:
            return {
                "result": False,
                "data": None,
                "message": "Cible refusee par l'hote.",
            }
        result = self.results.get(tool_name)
        if result is None:
            result = {"result": True, "data": {"ok": True}}
        if callable(result):
            result = result(dict(arguments))
        if isinstance(result, list):
            result = result.pop(0) if result else {"result": True, "data": None}
        if not isinstance(result, dict):
            result = {"result": True, "data": result}
        return copy.deepcopy(result)

    @staticmethod
    def _parse(payload: Any) -> Any:
        if not isinstance(payload, str):
            return payload
        try:
            return json.loads(payload)
        except Exception:
            return payload

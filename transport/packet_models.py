"""
transport/packet_models.py
==========================
Modèles de paquets de transport (Request, Response, Error) et paquets d'événements normalisés.
Garantit que les échanges entre l'Hôte (ex: AutoCUse RPA ou autre) et ManAgent sont structurés,
agnostiques et typés.

Utilise pydantic si disponible, avec fallback gracieux sur dataclasses en environnement sans dépendance externe.
"""

from typing import Optional, Dict, Any, List
import json

try:
    from pydantic import BaseModel, Field

    class RequestPacket(BaseModel):
        """Paquet de requête envoyé par l'hôte à ManAgent."""
        action: str
        payload: Dict[str, Any] = Field(default_factory=dict)

    class ResponsePacket(BaseModel):
        """Paquet de réponse synchrone de ManAgent à l'hôte."""
        type: str = "response"
        status: str = "success"
        payload: Dict[str, Any] = Field(default_factory=dict)

    class ErrorPacket(BaseModel):
        """Paquet d'erreur de ManAgent vers l'hôte."""
        type: str = "error"
        message: str

    class EventPacket(BaseModel):
        """Paquet d'événement général émis vers l'hôte."""
        type: str = "event"
        event: str
        payload: Dict[str, Any] = Field(default_factory=dict)

    class CheckpointReachedEvent(BaseModel):
        """Événement émis lors du franchissement validé d'un checkpoint sémantique."""
        skill_id: str
        version: int
        checkpoint_id: str
        checkpoint_name: str
        is_critical: bool = True
        reached_at: float
        observed_state: Dict[str, Any] = Field(default_factory=dict)

    class BreakoutOccurredEvent(BaseModel):
        """Événement émis lorsqu'un écart ou une anomalie (Breakout) est détecté(e)."""
        skill_id: str
        version: int
        step_id: Optional[str] = None
        breakout_type: str = "UNKNOWN"
        reason: str = ""
        occurred_at: float
        context_snapshot: Dict[str, Any] = Field(default_factory=dict)
        suggested_action: Optional[str] = None

    class ExecutionCompletedEvent(BaseModel):
        """Événement émis à la fin d'une exécution de Skill ou de séquence d'outils."""
        skill_id: Optional[str] = None
        version: Optional[int] = None
        success: bool
        duration_ms: float
        total_steps: int = 0
        passed_checkpoints: List[str] = Field(default_factory=list)
        output_data: Dict[str, Any] = Field(default_factory=dict)
        error_message: Optional[str] = None

except ImportError:
    from dataclasses import dataclass, field, asdict

    @dataclass
    class RequestPacket:
        action: str
        payload: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ResponsePacket:
        type: str = "response"
        status: str = "success"
        payload: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ErrorPacket:
        type: str = "error"
        message: str = ""

    @dataclass
    class EventPacket:
        type: str = "event"
        event: str = ""
        payload: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class CheckpointReachedEvent:
        skill_id: str
        version: int
        checkpoint_id: str
        checkpoint_name: str
        is_critical: bool = True
        reached_at: float = 0.0
        observed_state: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class BreakoutOccurredEvent:
        skill_id: str
        version: int
        step_id: Optional[str] = None
        breakout_type: str = "UNKNOWN"
        reason: str = ""
        occurred_at: float = 0.0
        context_snapshot: Dict[str, Any] = field(default_factory=dict)
        suggested_action: Optional[str] = None

    @dataclass
    class ExecutionCompletedEvent:
        skill_id: Optional[str] = None
        version: Optional[int] = None
        success: bool = True
        duration_ms: float = 0.0
        total_steps: int = 0
        passed_checkpoints: List[str] = field(default_factory=list)
        output_data: Dict[str, Any] = field(default_factory=dict)
        error_message: Optional[str] = None

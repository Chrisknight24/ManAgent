"""
core/discovery/models.py
========================
Modèles de données pour le Discovery Framework.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, model_validator
from datetime import datetime
import uuid
import json
from core.i18n import _


class StepType(str, Enum):
    TOOL = "tool"
    SEMANTIC = "semantic"


class ExitPolicy(str, Enum):
    PLAN_COMPLETED = "plan_completed"
    GOAL_REACHED = "goal_reached"
    EXPECTED_RESULT_FOUND = "expected_result_found"
    TOOL_FAILED = "tool_failed"
    MAX_ITERATIONS = "max_iterations"
    USER_CANCELLED = "user_cancelled"
    INVALID_PLAN = "invalid_plan"
    SECURITY_VIOLATION = "security_violation"
    GOAL_NOT_REACHED = "goal_not_reached"


class DiscoveryStep(BaseModel):
    id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    type: StepType
    description: str = Field(..., description="Description de l'étape (en langage naturel)")
    tool_name: Optional[str] = Field(None, description="Nom de l'outil (si type=TOOL)")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Arguments de l'outil")
    question: Optional[str] = Field(None, description="Question sémantique (si type=SEMANTIC)")
    expected_result: str = Field(
        default="true",
        description="Résultat attendu ('true', 'false', ou 'any')"
    )
    output_variable_name: Optional[str] = Field(
        None,
        description="Nom de la variable pour stocker le résultat"
    )


class DiscoveryPlan(BaseModel):
    goal: str = Field(...)
    steps: List[DiscoveryStep] = Field(...)
    data_type: str = Field(...)
    targets: List[str] = Field(..., description="Liste des cibles")
    technical_goals: List[str] = Field(..., description="Liste des goals techniques")
    signature: Optional[str] = Field(None, description="Signature normalisée")


class DiscoveryRequest(BaseModel):
    discovery_needed: bool = Field(default=True, description="True si tu souhaites obtenir des informations sur des données")
    goal: str = Field(..., description="Objectif de la découverte")
    data_type: str = Field(..., description="Type de données")
    targets: List[str] = Field(default_factory=lambda: ["recent"], description="Liste des cibles (au moins une)")
    technical_goals: List[str] = Field(default_factory=lambda: ["get_recent_history"], description="Liste des goals techniques (même longueur)")

    @model_validator(mode='before')
    @classmethod
    def sanitize_and_autofill(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normaliser target / targets
            if "target" in data and ("targets" not in data or not data["targets"]):
                t_val = data.get("target")
                data["targets"] = [t_val] if isinstance(t_val, str) else (list(t_val) if t_val else [])
            elif "targets" in data and isinstance(data["targets"], str):
                data["targets"] = [data["targets"]]

            # Normaliser technical_goal / technical_goals
            if "technical_goal" in data and ("technical_goals" not in data or not data["technical_goals"]):
                tg_val = data.get("technical_goal")
                data["technical_goals"] = [tg_val] if isinstance(tg_val, str) else (list(tg_val) if tg_val else [])
            elif "technical_goals" in data and isinstance(data["technical_goals"], str):
                data["technical_goals"] = [data["technical_goals"]]

            dt = str(data.get("data_type", "")).lower()
            
            # Auto-complétion des cibles si manquantes
            targets = data.get("targets")
            if not targets:
                if dt == "history":
                    targets = ["recent"]
                elif dt == "facts":
                    targets = ["all_facts"]
                elif dt == "missions":
                    targets = ["last_mission"]
                elif dt == "registry":
                    targets = ["all_keys"]
                else:
                    targets = ["recent"]
                data["targets"] = targets

            # Auto-complétion des goals techniques si manquants
            technical_goals = data.get("technical_goals")
            if not technical_goals:
                if dt == "history":
                    technical_goals = ["get_recent_history"]
                elif dt == "facts":
                    technical_goals = ["list_facts"]
                elif dt == "missions":
                    technical_goals = ["get_mission_details"]
                elif dt == "registry":
                    technical_goals = ["list_keys"]
                else:
                    technical_goals = ["get_recent_history"]
                data["technical_goals"] = technical_goals

            # Harmonisation des longueurs
            if isinstance(targets, list) and isinstance(technical_goals, list):
                if len(targets) > len(technical_goals) and len(technical_goals) == 1:
                    data["technical_goals"] = technical_goals * len(targets)
                elif len(technical_goals) > len(targets) and len(targets) == 1:
                    data["targets"] = targets * len(technical_goals)
                elif len(targets) != len(technical_goals):
                    min_len = min(len(targets), len(technical_goals))
                    data["targets"] = targets[:min_len]
                    data["technical_goals"] = technical_goals[:min_len]

        return data

    @model_validator(mode='after')
    def validate_targets_consistency(self) -> 'DiscoveryRequest':
        if not self.targets or not self.technical_goals:
            raise ValueError(_("'targets' et 'technical_goals' sont requis."))
        if len(self.targets) != len(self.technical_goals):
            raise ValueError(_("Les listes 'targets' et 'technical_goals' doivent avoir la même longueur."))
        return self

class WorkspaceEntry(BaseModel):
    step_id: str
    question: str
    answer: str
    tool_name: Optional[str] = None
    tool_args_raw: Optional[Dict[str, Any]] = None
    tool_result: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class RefinedContext(BaseModel):
    signature: str
    data_type: str
    targets: List[str] = Field(..., description="Liste des cibles")
    technical_goals: List[str] = Field(..., description="Liste des goals techniques")
    goal: str
    entries: List[WorkspaceEntry] = Field(default_factory=list)
    summary: str
    exit_policy: ExitPolicy
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    version: int = 1


class DiscoverySessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: f"ds_{uuid.uuid4().hex[:12]}")
    entity_id: str
    plan: DiscoveryPlan
    workspace: List[WorkspaceEntry] = Field(default_factory=list)
    current_step_index: int = 0
    status: str = "running"
    exit_policy: Optional[ExitPolicy] = None
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None


class ExplorerStep(BaseModel):
    type: Literal["tool", "semantic"] = Field(..., description="Type d'étape")
    description: str = Field(..., description="Description de l'étape")
    tool_name: Optional[str] = Field(None, description="Nom de l'outil (si type='tool')")
    tool_args_json: str = Field(
        default="{}",
        description="Chaîne JSON contenant les arguments de l'outil (obligatoire si type='tool')"
    )
    question: Optional[str] = Field(None, description="Question sémantique (si type='semantic')")
    expected_result: str = Field("true", description="Résultat attendu : 'true', 'false' ou 'any'")

    @model_validator(mode='after')
    def validate_tool_args(self) -> 'ExplorerStep':
        if self.type == "tool":
            if not self.tool_name:
                raise ValueError(_("Une étape de type 'tool' doit avoir un tool_name."))
            try:
                args = json.loads(self.tool_args_json) if self.tool_args_json else {}
            except json.JSONDecodeError:
                raise ValueError(_("tool_args_json n'est pas un JSON valide."))
            if self.tool_name == "describe_value" and "target" not in args:
                raise ValueError(_("L'outil 'describe_value' nécessite un argument 'target' dans tool_args."))
        elif self.type == "semantic":
            if not self.question:
                raise ValueError(_("Une étape de type 'semantic' doit avoir une question."))
        return self

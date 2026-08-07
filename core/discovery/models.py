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
    """Type d'étape dans un DiscoveryPlan."""
    TOOL = "tool"
    SEMANTIC = "semantic"


class ExitPolicy(str, Enum):
    """Raison de terminaison d'une DiscoverySession."""
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
    """Une étape du DiscoveryPlan (généré par l'Explorer)."""
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
    """Plan d'investigation généré par l'Explorer."""
    goal: str = Field(..., description="Objectif de la découverte (exprimé par le LLM)")
    steps: List[DiscoveryStep] = Field(..., description="Étapes à exécuter")
    data_type: str = Field(..., description="Type de données à explorer")
    target: str = Field(..., description="Cible de la découverte")
    technical_goal: str = Field(..., description="Goal technique choisi (ex: 'check_value')")
    signature: Optional[str] = Field(None, description="Signature normalisée (générée par le Runtime)")


class DiscoveryRequest(BaseModel):
    """
    Demande de découverte émise par le LLM de l'entité.
    Le LLM choisit un technical_goal parmi ceux exposés pour le data_type.
    """
    discovery_needed: bool = Field(
        True,
        description="True si une découverte est nécessaire."
    )
    goal: str = Field(
        ...,
        description="Objectif de la découverte en langage naturel (ex: 'Vérifier si l'image contient un lapin')"
    )
    data_type: str = Field(
        ...,
        description="Type de données à explorer (ex: 'registry')"
    )
    target: str = Field(
        ...,
        description="Cible de la découverte (ex: 'img_data')"
    )
    technical_goal: str = Field(
        ...,
        description="Goal technique choisi parmi la liste disponible pour ce data_type (ex: 'check_value')"
    )


class WorkspaceEntry(BaseModel):
    """Une entrée dans le Workspace (question/réponse)."""
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
    target: str
    goal: str
    technical_goal: str
    entries: List[WorkspaceEntry] = Field(default_factory=list)
    summary: str
    exit_policy: ExitPolicy
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    version: int = 1


class DiscoverySessionState(BaseModel):
    """État interne d'une DiscoverySession en cours d'exécution."""
    session_id: str = Field(default_factory=lambda: f"ds_{uuid.uuid4().hex[:12]}")
    entity_id: str
    plan: DiscoveryPlan
    workspace: List[WorkspaceEntry] = Field(default_factory=list)
    current_step_index: int = 0
    status: str = "running"
    exit_policy: Optional[ExitPolicy] = None
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None


# =====================================================
# NOUVEAU : ExplorerStep avec tool_args_json
# =====================================================

class ExplorerStep(BaseModel):
    """
    Une étape produite par le LLM de l'Explorer.
    L'Explorer transforme cette liste en DiscoveryPlan.
    """
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
        """Valide que les arguments sont cohérents avec le type d'étape."""
        if self.type == "tool":
            if not self.tool_name:
                raise ValueError(_("Une étape de type 'tool' doit avoir un tool_name."))
            # Vérifier que tool_args_json est un JSON valide
            try:
                args = json.loads(self.tool_args_json) if self.tool_args_json else {}
            except json.JSONDecodeError:
                raise ValueError(_("tool_args_json n'est pas un JSON valide."))
            # Vérification spécifique à l'outil (ex: describe_value nécessite target)
            if self.tool_name == "describe_value" and "target" not in args:
                raise ValueError(_("L'outil 'describe_value' nécessite un argument 'target' dans tool_args."))
            # On pourrait ajouter d'autres vérifications ici
        elif self.type == "semantic":
            if not self.question:
                raise ValueError(_("Une étape de type 'semantic' doit avoir une question."))
        return self
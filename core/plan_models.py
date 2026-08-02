"""
plan_models.py
==============
Le contrat de données strict pour l'architecture 'Tout est Plan'.
Mis à jour avec la méthode Stringified JSON pour les arguments d'outils,
la logique conditionnelle par registre et la prévention des appels vides.
Version avec héritage BaseDiscoverySchema pour la Progressive Disclosure.
"""

import json
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator
from core.constants import OrchestratorMode
from core.i18n import _
from core.execution_models import ExecutionTree
from core.execution_models import ExecutionTree, FailureClass
from core.base_schema import BaseDiscoverySchema  # <-- NOUVEAU

# =====================================================
# ÉNUMÉRATIONS
# =====================================================
class StepType(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    TOOL_CALL = "tool_call"
    ABSTRACT_TASK = "abstract_task"

class ExecutionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    MAX_RETRIES_REACHED = "max_retries_reached"
    RUNNING = "running"

# =====================================================
# MODÈLES (héritent désormais de BaseDiscoverySchema)
# =====================================================

class FeasibilityDecision(BaseDiscoverySchema):
    """Décision initiale du Solver sur la faisabilité de l'objectif."""
    is_possible: bool = Field(
        ..., 
        description=_("True si les outils disponibles permettent de résoudre le but, False sinon.")
    )
    reason: str = Field(
        ..., 
        description=_("Si is_possible est False, l'explication polie et directe destinée à l'utilisateur. Si True, une courte justification technique.")
    )
    refined_strategy: str = Field(
        default="", 
        description=_("La stratégie globale d'approche à transmettre au Planner. Laisser vide si is_possible est False.")
    )


class PlanStep(BaseModel):
    id: str = Field(..., description=_("Identifiant unique de l'étape (ex: step_1)"))
    description: str = Field(..., description=_("Ce que cette étape est censée accomplir"))
    type: StepType = Field(..., description=_("Le type d'action à mener pour cette étape"))
    
    step_context: str = Field(
        default="", 
        description=_("Le contexte local, données ou prérequis spécifiques nécessaires à l'exécution.")
    )
    
    expected_result: str = Field(
        ..., 
        description=_("CRITIQUE : Si type == 'tool_call', doit valoir 'true', 'false' ou 'any' ('any' pour capturer le statut binaire sans faire crasher le plan en cas d'échec légitime). Si type == 'abstract_task', décrit sémantiquement l'état attendu.")
    )

    output_variable_name: Optional[str] = Field(
        None, 
        description=_("Nom de la variable (ex: 'notepad_ouvert',) pour stocker le résultat (True/False ou Data) de CETTE étape dans le registre, quel que soit son type.")
    )
    output_variable_desc: Optional[str] = Field(
        None,
        description=_("Description de ce que représente cette donnée dans le registre.")
    )

    execute_if: Optional[str] = Field(
        None,
        description=_("Condition déterministe STRICTEMENT booléenne. Ex: '$@_nom == True'.")
    )

    @model_validator(mode='after')
    def validate_tool_integrity(self) -> 'PlanStep':
        if self.type == StepType.TOOL_CALL:
            if not self.tool_name or not str(self.tool_name).strip():
                raise ValueError(_("Un step de type 'tool_call' exige obligatoirement un 'tool_name' non vide."))
        
        if self.execute_if:
            v_lower = self.execute_if.lower()
            if "_data" in v_lower:
                raise ValueError("CRITICAL REJECTION: Le champ 'execute_if' ne doit jamais analyser les variables de données complexes (ex: $@_nom_data). Utilisez uniquement le signal binaire $@_nom.")
            if "." in self.execute_if:
                raise ValueError("CRITICAL REJECTION: La notation pointée (ex: .result, .data) est strictement prohibée dans 'execute_if'.")
            if "contains" in v_lower or " in " in v_lower:
                raise ValueError("CRITICAL REJECTION: Les opérateurs sémantiques (IN, CONTAINS) sont interdits dans l'infrastructure de flux. Utilisez des outils dédiés en amont.")
                
        return self

    response_text: Optional[str] = Field(None)
    tool_name: Optional[str] = Field(None)
    tool_args_json: str = Field(default="{}")
    
    status: ExecutionStatus = ExecutionStatus.PENDING
    result_context: str = ""

    @property
    def get_parsed_args(self) -> dict:
        try:
            return json.loads(self.tool_args_json)
        except Exception:
            return {}
    

class Plan(BaseDiscoverySchema):
    """Le plan global proposé par un Solver."""
    goal: str = Field(..., description=_("L'objectif global de ce plan"))
    steps: List[PlanStep] = Field(default_factory=list, description=_("Liste ordonnée des étapes à exécuter"))
    
    def is_complete(self) -> bool:
        return all(step.status in [ExecutionStatus.SUCCESS, ExecutionStatus.SKIPPED] for step in self.steps)


class SolverResult(BaseModel):
    """Contrat de retour unifié du Solver vers l'Orchestrateur."""
    status: ExecutionStatus
    final_context: str 
    response: str = "" 
    error_reason: Optional[str] = None
    resolved_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description=_("Données du registre local renvoyées au parent."))
    execution_tree: Optional[ExecutionTree] = None
    failure_class: Optional[FailureClass] = Field(
        None,
        description=_("Classe d'échec détectée par l'Executor (EXECUTION_FAILURE ou CONVERGENCE_FAILURE).")
    )
    target_entity: Optional[str] = Field(
        None,
        description=_("Entité tenue pour responsable, fixée par le code au point d'échec exact (Executor pour EXECUTION_FAILURE/CONVERGENCE_FAILURE).")
    )


class MissionSignature(BaseModel):
    action: str = Field(..., description="L'action à effectuer (ex: ouvrir, fermer, lancer)")
    object: str = Field(..., description="L'objet de l'action (ex: chrome, excel, notepad)")
    desired_state: Optional[str] = Field(None, description="État final souhaité (optionnel, ex: ouvert, fermé)")


class OrchestratorDecision(BaseDiscoverySchema):
    type: OrchestratorMode = Field(..., description=_("Mode de traitement : direct ou mission"))
    output: str = Field(..., description=_("Réponse utilisateur directe OU description analytique de la mission."))
    signatures: List[MissionSignature] = Field(
        default_factory=list,
        description=_("Liste des missions simples extraites de la demande, si applicable.")
    )


class ConvergenceDecision(BaseDiscoverySchema):
    is_convergent: bool = Field(
        ..., 
        description=_("True si le résultat réel de l'action remplit et valide l'output attendu, False sinon.")
    )
    reason: str = Field(
        ..., 
        description=_("Analyse technique de la convergence ou explication précise de la divergence constatée.")
    )


class CompactedAdvice(BaseDiscoverySchema):
    advice: str = Field(..., description="Conseil stratégique (stratégies clés + pièges à éviter) pour le Planner.")
    is_novel: bool = Field(..., description="True si la mission actuelle semble réellement nouvelle (peu de patterns connus), False si elle est déjà bien couverte par les missions similaires.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Niveau de confiance du compactor dans son jugement (optionnel).")
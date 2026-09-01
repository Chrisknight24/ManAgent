# core/execution_models.py
# =====================================================
# MODÈLES DE TÉLÉMÉTRIE STRUCTURÉE (Phase 0 – v2)
# Ces modèles permettent de retracer fidèlement l'exécution
# d'une mission, y compris les sous-tâches et les tentatives.
# =====================================================

from typing import List, Optional, Dict, Any, ForwardRef
from pydantic import BaseModel, Field
from enum import Enum
import time
import uuid

from core.i18n import _  # pour les messages potentiellement visibles

# ---------------------------------------------
# Énumération des classes d'échec pour une tentative
# ---------------------------------------------
class FailureClass(str, Enum):
    """Catégorie d'échec d'une tentative de plan."""
    NONE = "none"                                 # Pas d'échec (tentative réussie)
    PLAN_REJECTED_VALIDATION = "plan_rejected_validation"   # Échec de validation statique du plan (ValueError)
    PLAN_REJECTED_SUPERVISOR = "plan_rejected_supervisor"   # Rejet par le superviseur (Orchestrator ou parent)
    EXECUTION_FAILURE = "execution_failure"                 # Échec lors de l'exécution d'une étape (outil ou convergence)
    CONVERGENCE_FAILURE = "convergence_failure"             # Échec de convergence sémantique (détectée par LLM)
    USER_CANCELLED = "user_cancelled"                       # Annulation par l'utilisateur
    MAX_RETRIES_REACHED = "max_retries_reached"             # Épuisement des tentatives sans succès

# ---------------------------------------------
# Nœud d'exécution (représente une étape ou une sous-tâche)
# ---------------------------------------------
class ExecutionNode(BaseModel):
    """
    Un nœud dans l'arbre d'exécution.
    Peut représenter une étape atomique (tool_call, direct_answer)
    ou une sous-tâche abstraite (abstract_task) qui aura son propre ExecutionTree.
    """
    # Identifiants
    node_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=_("Identifiant unique du nœud (UUID)")
    )
    step_id: str = Field(..., description=_("ID de l'étape (ex: step_1)"))
    description: str = Field(..., description=_("Description textuelle de l'étape"))
    step_type: str = Field(..., description=_("Type d'étape (tool_call, abstract_task, direct_answer)"))

    # Pour les tool_call
    tool_name: Optional[str] = Field(None, description=_("Nom de l'outil si step_type == tool_call"))
    tool_args: Optional[Dict[str, Any]] = Field(None, description=_("Arguments de l'outil (dict)"))

    # Résultats attendus et réels
    expected_result: Optional[str] = Field(None, description=_("Critère de réussite attendu"))
    actual_result: Optional[str] = Field(None, description=_("Résultat réel obtenu"))

    # --- A.3 (NOUVEAU) : le booléen BRUT renvoyé par l'outil C++, indépendamment de ce que
    # l'analyse rigide en a fait. Sans ce champ, on ne pouvait pas distinguer "cette étape a
    # réussi" de "cette étape a été TOLÉRÉE malgré un échec réel, parce que expected_result
    # valait 'any'" — exactement l'ambiguïté rencontrée sur un kill_process en test, où
    # personne (humain ou LLM) ne pouvait trancher sans deviner.
    raw_tool_success: Optional[bool] = Field(
        None,
        description=_("Valeur brute 'result' renvoyée par l'outil C++ (avant tout jugement de convergence). None si non applicable (direct_answer, abstract_task).")
    )
    raw_success_flag: Optional[str] = Field(
        None,
        description=_("Statut brut ('true'/'false'/etc.) renvoyé par l'outil C++.")
    )

    # Statut et erreur
    status: str = Field(..., description=_("Statut final de l'étape (success, failed, skipped, pending)"))
    error_reason: Optional[str] = Field(None, description=_("Si échec, raison détaillée"))

    # Horodatage
    started_at: Optional[float] = Field(None, description=_("Timestamp de début (time.time())"))
    ended_at: Optional[float] = Field(None, description=_("Timestamp de fin (time.time())"))

    # Métadonnées extensibles (pour stocker des infos supplémentaires)
    metadata: Dict[str, Any] = Field(default_factory=dict, description=_("Métadonnées libres"))

    # Récursivité : pour les abstract_task, l'arbre complet du solver enfant
    child_execution_tree: Optional['ExecutionTree'] = Field(
        None,
        description=_("Arbre d'exécution du solver enfant (pour les sous-tâches abstraites)")
    )

    # Calcul de la durée
    @property
    def duration(self) -> Optional[float]:
        if self.started_at is not None and self.ended_at is not None:
            return self.ended_at - self.started_at
        return None

# ---------------------------------------------
# Une tentative de plan (PlanAttempt)
# ---------------------------------------------
class PlanAttempt(BaseModel):
    """
    Une tentative d'exécution d'un plan pour un Solver donné.
    Correspond à une itération de la boucle `for current_try in range(max_tries)`.
    """
    attempt_number: int = Field(..., description=_("Numéro de la tentative (1-indexé)"))
    started_at: Optional[float] = Field(None, description=_("Début de la tentative"))
    ended_at: Optional[float] = Field(None, description=_("Fin de la tentative"))

    # Le plan qui a été proposé pour cette tentative (sérialisé en JSON)
    proposed_plan: Optional[Dict[str, Any]] = Field(
        None,
        description=_("Plan proposé (dict sérialisable) pour cette tentative")
    )

    # Les nœuds d'exécution générés par cette tentative
    nodes: List[ExecutionNode] = Field(
        default_factory=list,
        description=_("Liste des nœuds d'exécution de cette tentative")
    )

    # Résultat global
    outcome: str = Field(default="in_progress", description=_("Résultat global de la tentative (in_progress, success, failed)"))
    failure_class: FailureClass = Field(
        default=FailureClass.NONE,
        description=_("Catégorie d'échec si outcome == failed")
    )
    failure_reason: Optional[str] = Field(
        None,
        description=_("Message d'erreur détaillé en cas d'échec")
    )

    # ---> NOUVEAU : Blâme explicite, posé à la source par Solver/Executor.
    # Ne JAMAIS reconstruire cette valeur après coup depuis failure_class :
    # failure_class décrit un mécanisme d'échec, pas une entité responsable
    # (ex: PLAN_REJECTED_SUPERVISOR ne dit rien sur qui a proposé le plan).
    target_entity: Optional[str] = Field(
        None,
        description=_("Entité tenue pour responsable de cet échec (ex: 'Planner', 'Orchestrator', 'Executor'), fixée par le code au moment de l'échec.")
    )

    # Feedback du Planner (pour les erreurs de validation)
    planner_feedback: Optional[str] = Field(
        None,
        description=_("Feedback du Planner en cas d'erreur de validation")
    )

    # --- A.4 (NOUVEAU) : le conseil du reranker existait déjà, mais uniquement en log
    # éphémère (Logger.debug) — impossible à relire après coup. Ce champ le persiste
    # avec la tentative elle-même : on peut désormais répondre à "quel conseil a
    # influencé CE plan précis ?" en relisant l'épisode, pas en fouillant des logs.
    advice_injected: Optional[str] = Field(
        None,
        description=_("Texte du conseil (leçons retenues par le reranker) effectivement injecté dans le prompt du Planner pour cette tentative, s'il y en a eu un.")
    )

    # Méthode utilitaire pour ajouter un nœud
    def add_node(self, node: ExecutionNode):
        self.nodes.append(node)

    @property
    def duration(self) -> Optional[float]:
        if self.started_at is not None and self.ended_at is not None:
            return self.ended_at - self.started_at
        return None

# ---------------------------------------------
# Arbre d'exécution complet pour un Solver
# ---------------------------------------------
class ExecutionTree(BaseModel):
    """
    Arbre d'exécution pour un Solver donné.
    Contient toutes les tentatives (PlanAttempt) de ce Solver.
    """
    solver_id: str = Field(..., description=_("ID du Solver"))
    goal: Optional[str] = Field(None, description=_("Objectif du solver"))

    # Contexte hiérarchique
    parent_solver_id: Optional[str] = Field(None, description=_("ID du solver parent (si enfant)"))
    parent_step_id: Optional[str] = Field(None, description=_("ID de l'étape parente (si enfant)"))
    depth: int = Field(0, description=_("Profondeur dans l'arbre (0 pour root)"))

    # Horodatage global
    started_at: Optional[float] = Field(None, description=_("Début du solver"))
    ended_at: Optional[float] = Field(None, description=_("Fin du solver"))
    status: str = Field(default="in_progress", description=_("Statut final du solver (in_progress, success, failed)"))

    # Liste des tentatives
    attempts: List[PlanAttempt] = Field(
        default_factory=list,
        description=_("Liste des tentatives")
    )

    # Méthode utilitaire pour ajouter une tentative
    def add_attempt(self, attempt: PlanAttempt):
        self.attempts.append(attempt)

    # Pour obtenir la dernière tentative
    @property
    def last_attempt(self) -> Optional[PlanAttempt]:
        if self.attempts:
            return self.attempts[-1]
        return None

# Résolution des forward references pour Pydantic
ExecutionNode.model_rebuild()

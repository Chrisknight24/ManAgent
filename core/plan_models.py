"""
plan_models.py
==============
Le contrat de données strict pour l'architecture 'Tout est Plan'.
Mis à jour avec la méthode Stringified JSON pour les arguments d'outils,
la logique conditionnelle par registre et la prévention des appels vides.
Version avec héritage BaseDiscoverySchema pour la Progressive Disclosure.
Ajout : champ is_crucial et validation stricte du nommage des variables.
"""

import json
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator
from core.constants import OrchestratorMode
from core.i18n import _
from core.execution_models import ExecutionTree
from core.execution_models import ExecutionTree, FailureClass
from core.base_schema import BaseDiscoverySchema
from utils.logger import Logger  # <-- NOUVEAU pour les warnings
from core.discovery.models import DiscoveryRequest
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

class RiskLevel(str, Enum):
    """Niveau de risque perçu d'un plan par la validation finale de l'Orchestrateur."""
    LOW = "low"
    MEDIUM = "medium"
    CRITICAL = "critical"

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
        description=_("Condition déterministe STRICTEMENT booléenne. Ex: '$@_bool_nom == True'.")
    )

    # --- NOUVEAU : Variable cruciale pour le RUM ---
    is_crucial: bool = Field(
        False,
        description=_("Si True, cette variable sera ajoutée au Registre Utile de Mission (RUM) visible par le Présentateur. Utilisez ce flag pour les preuves directes de succès/échec de la mission.")
    )

    # --- NOUVEAU : déclaration d'irréversibilité (validation finale Orchestrateur) ---
    is_irreversible: bool = Field(
        False,
        description=_(
            "Si True, cette étape a un effet difficilement ou pas réversible une fois exécuté "
            "(suppression, envoi, paiement, écrasement de données, etc.). C'est une DÉCLARATION "
            "du Planner, pas une déduction automatique : soyez honnête, l'Orchestrateur peut "
            "contester ce jugement lors de la validation finale mais part de votre déclaration."
        )
    )
    irreversibility_reason: Optional[str] = Field(
        None,
        description=_("Si is_irreversible=True, explique brièvement en quoi l'effet est irréversible.")
    )

    @model_validator(mode='after')
    def validate_tool_integrity(self) -> 'PlanStep':
        if self.type == StepType.TOOL_CALL:
            if not self.tool_name or not str(self.tool_name).strip():
                raise ValueError(_("Un step de type 'tool_call' exige obligatoirement un 'tool_name' non vide."))
        
        if self.execute_if:
            v_lower = self.execute_if.lower()
            if "_data" in v_lower:
                raise ValueError("CRITICAL REJECTION: Le champ 'execute_if' ne doit jamais analyser les variables de données complexes (ex: $@_data_nom). Utilisez uniquement le signal binaire $@_bool_nom.")
            if "." in self.execute_if:
                raise ValueError("CRITICAL REJECTION: La notation pointée (ex: .result, .data) est strictement prohibée dans 'execute_if'.")
            if "contains" in v_lower or " in " in v_lower:
                raise ValueError("CRITICAL REJECTION: Les opérateurs sémantiques (IN, CONTAINS) sont interdits dans l'infrastructure de flux. Utilisez des outils dédiés en amont.")
                
        return self

    # --- NOUVEAU : Validation stricte du nommage des variables ---
    @model_validator(mode='after')
    def validate_variable_naming(self) -> 'PlanStep':
        if self.output_variable_name:
            if not (self.output_variable_name.startswith("bool_") or self.output_variable_name.startswith("data_")):
                # On lève une erreur pour forcer le Planner à respecter la convention
                raise ValueError(
                    _("La variable '{}' ne respecte pas la convention de nommage. Elle doit commencer par 'bool_' ou 'data_'.")
                    .format(self.output_variable_name)
                )
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
    failure_bundle: Optional[Any] = None
    breakout_report: Optional[Any] = None
    failure_class: Optional[FailureClass] = Field(
        None,
        description=_("Classe d'échec détectée par l'Executor (EXECUTION_FAILURE ou CONVERGENCE_FAILURE).")
    )
    target_entity: Optional[str] = Field(
        None,
        description=_("Entité tenue pour responsable, fixée par le code au point d'échec exact (Executor pour EXECUTION_FAILURE/CONVERGENCE_FAILURE).")
    )


import re
import hashlib
from typing import List, Optional, Dict, Any

FR_TO_EN_ACTIONS = {
    "ouvrir": "open",
    "fermer": "close",
    "lancer": "launch",
    "demarrer": "start",
    "démarrer": "start",
    "cliquer": "click",
    "taper": "type",
    "ecrire": "type",
    "écrire": "type",
    "saisir": "type",
    "appuyer": "press",
    "presser": "press",
    "creer": "create",
    "créer": "create",
    "supprimer": "delete",
    "rechercher": "search",
    "chercher": "search",
    "telecharger": "download",
    "télécharger": "download",
    "executer": "execute",
    "exécuter": "execute",
    "afficher": "show",
    "masquer": "hide",
    "lire": "read",
    "copier": "copy",
    "coller": "paste",
}

FR_TO_EN_OBJECTS = {
    "boite de dialogue executer": "run dialog box",
    "boîte de dialogue exécuter": "run dialog box",
    "boite de dialogue \"executer\"": "run dialog box",
    "boîte de dialogue \"exécuter\"": "run dialog box",
    "dialogue executer": "run dialog box",
    "dialogue exécuter": "run dialog box",
    "dialogue 'executer'": "run dialog box",
    "dialogue 'exécuter'": "run dialog box",
    "menu demarrer": "start menu",
    "menu démarrer": "start menu",
    "menu demarrer - executer": "run dialog box",
    "menu démarrer - exécuter": "run dialog box",
    "navigateur": "browser",
    "navigateur web": "web browser",
    "calculatrice": "calculator",
    "bloc-notes": "notepad",
    "bloc notes": "notepad",
    "explorateur de fichiers": "file explorer",
    "gestionnaire de taches": "task manager",
    "gestionnaire de tâches": "task manager",
    "invite de commandes": "command prompt",
}

def clean_signature_str(val: Optional[str]) -> str:
    if not val:
        return ""
    # Strip quotes, backticks, parenthesis, braces, brackets
    cleaned = re.sub(r'["\'`«»()\[\]{}]', '', val)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()
    return cleaned

class MissionSignature(BaseModel):
    action: str = Field(..., description="The action to perform in English, infinitive verb without punctuation (e.g. open, close, launch, click, type, press)")
    object: str = Field(..., description="The target entity/object in English without quotes (e.g. run dialog box, start menu, notepad, browser)")
    desired_state: Optional[str] = Field(None, description="Desired final state in English (optional, e.g. open, closed, focused, active)")

    @model_validator(mode='after')
    def normalize_signature(self) -> 'MissionSignature':
        """Nettoie et normalise en anglais canonique la signature."""
        cleaned_act = clean_signature_str(self.action)
        cleaned_obj = clean_signature_str(self.object)
        
        # Fallback automatique traduction FR -> EN si le LLM a produit du français
        if cleaned_act in FR_TO_EN_ACTIONS:
            cleaned_act = FR_TO_EN_ACTIONS[cleaned_act]
            
        if cleaned_obj in FR_TO_EN_OBJECTS:
            cleaned_obj = FR_TO_EN_OBJECTS[cleaned_obj]
            
        self.action = cleaned_act
        self.object = cleaned_obj
        
        if self.desired_state:
            cleaned_state = clean_signature_str(self.desired_state)
            if cleaned_state in {"ouvert", "ouverte"}:
                cleaned_state = "open"
            elif cleaned_state in {"fermé", "fermée"}:
                cleaned_state = "closed"
            self.desired_state = cleaned_state
            
        return self

    def to_hash(self) -> str:
        """Génère un hash/signature canonique normalisé pour l'indexation et le retrieval de Skills."""
        act = clean_signature_str(self.action)
        obj = clean_signature_str(self.object)
        if self.desired_state:
            state = clean_signature_str(self.desired_state)
            return f"sig:{act}:{obj}:{state}"
        return f"sig:{act}:{obj}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "object": self.object,
            "desired_state": self.desired_state
        }

class AssetInjection(BaseModel):
    uri: str = Field(..., description="L'URI exact de la ressource (ex: inputs://turn_1, files://photo.jpg)")
    variable_name: str = Field(..., description="Le nom de la variable à créer (doit commencer par data_, ex: data_target_image)")
    description: str = Field(..., description="Description de ce que contient cet asset pour aider le Planner")

class OrchestratorDecision(BaseModel):
    """
    Décision de l'Orchestrateur : choisir entre direct, mission ou request.
    """
    type: OrchestratorMode = Field(
        ...,
        description=_("Mode de traitement : direct, mission ou request")
    )
    output: str = Field(
        ...,
        description=_("Réponse utilisateur directe OU description analytique de la mission OU intention de la requête.")
    )
    signatures: List[MissionSignature] = Field(
        default_factory=list,
        description=_("Liste des missions simples extraites de la demande, si applicable (obligatoire si type='mission').")
    )
    injected_assets: List[AssetInjection] = Field(
        default_factory=list,
        description=_("Liste des assets à injecter dans la mémoire du Solver sous forme de variables (obligatoire s'il y a des fichiers/inputs nécessaires à la mission).")
    )
    discovery_request: Optional[DiscoveryRequest] = Field(
        ...,  # OBLIGATOIRE dans le JSON, mais peut être null
        description=_(
            "⚠️ OBLIGATOIRE : Ce champ est requis dans la réponse JSON (même pour le mettre à null). "
            "Remplissez‑le UNIQUEMENT si vous avez choisi le type 'request'. "
            "Dans ce cas, vous DEVEZ fournir les paramètres (goal, data_type, target, technical_goal). "
            "Pour les types 'direct' ou 'mission', mettez‑le à null."
        )
    )
    learned_facts: List[str] = Field(
        default_factory=list,
        description=_("Faits durables et préférences de l'utilisateur déduits de sa demande. "
                      "Utile pour construire la mémoire sémantique du système. "
                      "Laissez vide s'il n'y a pas de nouvelle information stable.")
    )

    @model_validator(mode='after')
    def validate_discovery_consistency(self) -> 'OrchestratorDecision':
        if self.type == OrchestratorMode.REQUEST:
            if self.discovery_request is None:
                raise ValueError(_("Le champ 'discovery_request' est obligatoire lorsque type='request'."))
            if not getattr(self.discovery_request, 'data_type', None) or not getattr(self.discovery_request, 'targets', None):
                raise ValueError(_("Le champ 'discovery_request' doit spécifier un 'data_type' et au moins une cible."))
        else:
            if self.discovery_request is not None:
                self.discovery_request = None
        return self
    

class ConvergenceDecision(BaseDiscoverySchema):
    is_convergent: bool = Field(
        ..., 
        description=_("True si le résultat réel de l'action remplit et valide l'output attendu, False sinon.")
    )
    reason: str = Field(
        ..., 
        description=_("Analyse technique de la convergence ou explication précise de la divergence constatée.")
    )


class PlanValidationDecision(BaseDiscoverySchema):
    """
    Décision de l'Orchestrateur (le "LLM Judge") sur la conformité d'un plan
    proposé par un Solver, avant exécution. Contrairement à FeasibilityDecision
    (est-ce POSSIBLE ?) et ConvergenceDecision (est-ce que le RÉSULTAT converge ?),
    celle-ci juge le PLAN lui-même : conformité à rules.md, patterns récursifs
    signalés, actions irréversibles déclarées par le Planner.
    """
    is_conformant: bool = Field(
        ...,
        description=_("True si le plan respecte les critères de rules.md et converge raisonnablement vers l'objectif, False sinon.")
    )
    reason: str = Field(
        ...,
        description=_(
            "Justification technique de la décision. Si is_conformant=False, doit être "
            "assez précise pour que le Planner puisse corriger son prochain plan. "
            "Ce texte est réinjecté tel quel dans le contexte du Planner en cas de refus."
        )
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description=_("Niveau de risque perçu du plan, indépendamment de sa conformité.")
    )
    requires_human_confirmation: bool = Field(
        default=False,
        description=_(
            "True si, même conforme, ce plan doit être confirmé par un humain avant "
            "exécution (action critique et/ou irréversible). Ignoré si is_conformant=False "
            "(un plan déjà rejeté n'a pas besoin d'être confirmé)."
        )
    )
    irreversibility_flags: List[str] = Field(
        default_factory=list,
        description=_(
            "Liste des identifiants d'étapes jugées irréversibles ou critiques par "
            "l'Orchestrateur — peut différer de ce que le Planner a déclaré via "
            "is_irreversible sur chaque PlanStep (l'Orchestrateur peut contester)."
        )
    )


class CompactedAdvice(BaseDiscoverySchema):
    advice: str = Field(..., description="Conseil stratégique (stratégies clés + pièges à éviter) pour le Planner.")
    is_novel: bool = Field(..., description="True si la mission actuelle semble réellement nouvelle (peu de patterns connus), False si elle est déjà bien couverte par les missions similaires.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Niveau de confiance du compactor dans son jugement (optionnel).")


class DepthEscalationDecision(BaseDiscoverySchema):
    """
    Décision de l'Orchestrateur sur une demande d'extension de la profondeur
    maximale de récursion (abstract_task imbriqués). Ne juge PAS un plan
    précis (contrairement à PlanValidationDecision) mais la CHAÎNE de
    sous-tâches ayant mené à ce point : est-ce une décomposition légitime
    d'un problème réellement complexe, ou un motif dégénéré (boucle,
    redite du même objectif sous des formulations différentes, absence de
    progression tangible d'un niveau à l'autre) ?
    """
    is_legitimate_complexity: bool = Field(
        ...,
        description=_(
            "True si la chaîne de sous-tâches reflète une décomposition légitime d'un "
            "problème réellement complexe (chaque niveau a un objectif distinct qui fait "
            "progresser la mission). False si c'est un motif récursif dégénéré : le même "
            "objectif reformulé, une boucle sans progression, ou une décomposition qui "
            "n'apporte rien de nouveau par rapport au niveau parent."
        )
    )
    reason: str = Field(
        ...,
        description=_("Justification technique, assez précise pour comprendre le jugement même en cas de refus.")
    )

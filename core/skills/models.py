"""
Module core.skills.models
Définition des contrats et structures de données pour le cycle de vie des Skills ManAgent.
Garantit l'immutabilité, l'exportabilité (package .skillpkg / bundle JSON),
l'indexation par signatures de mission et le typage strict.
Compatible Python standard (dataclasses) pour une exécution ultra-rapide et zéro dépendance externe.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
import time
import json


class SkillState(str, Enum):
    """Machine à états stricte pour le cycle de vie d'un Skill."""
    DRAFT = "DRAFT"              # Vient d'être synthétisé ou réparé, inactif
    SHADOW = "SHADOW"            # Candidat qualifié passivement sur traces sans effets de bord
    PRODUCTION = "PRODUCTION"    # Validé, sélectionnable par Solver / Planner
    DEGRADED = "DEGRADED"        # Fiabilité en baisse, priorité réduite
    QUARANTINE = "QUARANTINE"    # Suspendu temporairement (Circuit Breaker)
    HALF_OPEN = "HALF_OPEN"      # Réadmission sous surveillance étroite
    RETIRED = "RETIRED"          # Archivé, inactif


class ProvenanceType(str, Enum):
    """Origine de création ou modification du Skill."""
    DISTILLED = "DISTILLED"          # Distillé automatiquement depuis les missions réussies
    REPAIRED = "REPAIRED"            # Généré suite à une auto-réparation ciblée
    HUMAN_EDITED = "HUMAN_EDITED"    # Édité manuellement par un humain (Priorité absolue)
    IMPORTED = "IMPORTED"            # Importé depuis un package externe (.skillpkg)


class FailureClass(str, Enum):
    """Taxonomie minimale des causes d'échec de Skill."""
    TRANSIENT = "TRANSIENT"                  # Problème transitoire (ex: fenêtre temporairement indisponible)
    TIMING_DRIFT = "TIMING_DRIFT"            # Délai d'attente dépassé / latence d'affichage
    FOCUS_DRIFT = "FOCUS_DRIFT"              # Focus perdu ou fenêtre passée en arrière-plan
    UI_LOCATOR_DRIFT = "UI_LOCATOR_DRIFT"    # Sélecteur d'ancre ou élément visuel modifié
    STATE_DRIFT = "STATE_DRIFT"              # État système inattendu
    PARAMETER_ERROR = "PARAMETER_ERROR"      # Paramètre invalide fourni au Skill
    HOST_CAPABILITY_ERROR = "HOST_CAPABILITY_ERROR" # Capacité requise absente sur l'hôte
    SKILL_LOGIC_ERROR = "SKILL_LOGIC_ERROR"  # Erreur structurelle dans le graphe
    UNKNOWN = "UNKNOWN"


@dataclass
class Checkpoint:
    """
    Étape sémantique vérifiable dans un Skill.
    Permet de valider la progression sans comparer des micro-actions brutes (clics pixel-perfect).
    """
    checkpoint_id: str
    name: str
    description: str = ""
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    is_critical: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checkpoint":
        return cls(**data)


@dataclass
class ExecutionEnvironment:
    """
    Matrice de compatibilité environnementale pour l'exécution d'un Skill.
    Permet à ManAgent et à l'hôte de vérifier l'adéquation avant exécution.
    """
    target_os: List[str] = field(default_factory=lambda: ["windows", "linux", "macos"])
    min_os_version: Optional[str] = None
    min_resolution_width: Optional[int] = None
    min_resolution_height: Optional[int] = None
    supported_dpi_scale: List[float] = field(default_factory=lambda: [1.0, 1.25, 1.5, 2.0])
    required_apps: List[str] = field(default_factory=list)
    required_host_capabilities: List[str] = field(default_factory=list)
    environment_flags: Dict[str, Any] = field(default_factory=dict)

    def is_compatible(self, host_env: Dict[str, Any]) -> bool:
        """Vérifie de manière déterministe si l'environnement de l'hôte satisfait les exigences."""
        # 1. Vérification OS
        current_os = host_env.get("os", "").lower()
        if current_os and current_os not in [o.lower() for o in self.target_os]:
            return False

        # 2. Vérification Capacités Hôte
        host_caps = set(host_env.get("capabilities", []))
        for cap in self.required_host_capabilities:
            if cap not in host_caps:
                return False

        # 3. Vérification Résolution
        w = host_env.get("resolution_width")
        h = host_env.get("resolution_height")
        if self.min_resolution_width and w and w < self.min_resolution_width:
            return False
        if self.min_resolution_height and h and h < self.min_resolution_height:
            return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEnvironment":
        return cls(**data)


@dataclass
class TrustProfile:
    """Profil statistique et métriques de fiabilité d'une version de Skill."""
    success_count: int = 0
    failure_count: int = 0
    breakout_count: int = 0
    consecutive_failures: int = 0
    shadow_validation_count: int = 0
    distinct_context_count: int = 0
    last_success_timestamp: Optional[float] = None
    last_failure_timestamp: Optional[float] = None
    recent_execution_window: List[bool] = field(default_factory=list)

    def record_run(self, success: bool, is_breakout: bool = False, is_shadow: bool = False, context_id: Optional[str] = None):
        """Enregistre le résultat d'une exécution et met à jour les indicateurs."""
        now = time.time()
        if is_shadow:
            if success:
                self.shadow_validation_count += 1
            return

        if success:
            self.success_count += 1
            self.consecutive_failures = 0
            self.last_success_timestamp = now
        else:
            self.failure_count += 1
            self.consecutive_failures += 1
            self.last_failure_timestamp = now
            if is_breakout:
                self.breakout_count += 1

        self.recent_execution_window.append(success)
        if len(self.recent_execution_window) > 20:
            self.recent_execution_window.pop(0)

    @property
    def trust_score(self) -> float:
        """Calcule un score explicable de 0.0 à 1.0."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5  # Score neutre initial
        
        base_rate = self.success_count / total
        
        # Pénalité si échecs consécutifs récents
        penalty = min(0.4, self.consecutive_failures * 0.15)
        
        # Fenêtre glissante récente (poids accru aux runs récents)
        if self.recent_execution_window:
            recent_rate = sum(1 for r in self.recent_execution_window if r) / len(self.recent_execution_window)
            return max(0.0, min(1.0, (base_rate * 0.4) + (recent_rate * 0.6) - penalty))
        
        return max(0.0, min(1.0, base_rate - penalty))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustProfile":
        return cls(**data)


@dataclass
class SkillVersion:
    """
    Version immuable d'un Skill.
    Une version en PRODUCTION n'est jamais modifiée in-place.
    """
    skill_id: str
    version: int
    flow_payload_ref: str
    parent_version: Optional[int] = None
    state: SkillState = SkillState.DRAFT
    
    # Gouvernance et traçabilité agnostique du modèle (Carte d'Identité)
    creator_model: str = "unknown"
    creator_capabilities: List[str] = field(default_factory=list)
    min_reasoning_score: float = 1.0
    min_benchmark_score: float = 50.0
    min_capability_tier: int = 1
    provenance: ProvenanceType = ProvenanceType.DISTILLED
    repair_reason: Optional[str] = None
    
    # Métriques
    trust_profile: TrustProfile = field(default_factory=TrustProfile)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["state"] = self.state.value
        res["provenance"] = self.provenance.value
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillVersion":
        d = dict(data)
        if isinstance(d.get("state"), str):
            d["state"] = SkillState(d["state"])
        if isinstance(d.get("provenance"), str):
            d["provenance"] = ProvenanceType(d["provenance"])
        if isinstance(d.get("trust_profile"), dict):
            d["trust_profile"] = TrustProfile.from_dict(d["trust_profile"])
        return cls(**d)


@dataclass
class SkillManifest:
    """
    Manifeste déclarant l'identité, les paramètres et les signatures d'un Skill.
    Sert au pré-filtrage rapide (<2ms) et à la présentation au Solver / Planner.
    """
    skill_id: str
    name: str
    description: str
    namespace: str = "default"
    
    # Schéma des paramètres d'entrée (JSON Schema standard)
    parameters_schema: Dict[str, Any] = field(default_factory=dict)
    
    # Indexation par signatures de mission pour préfiltrage instantané
    signature_hashes: List[str] = field(default_factory=list)
    target_applications: List[str] = field(default_factory=list)
    
    # Environnement & Contraintes
    environment: ExecutionEnvironment = field(default_factory=ExecutionEnvironment)
    checkpoints: List[Checkpoint] = field(default_factory=list)
    risk_level: str = "low"
    
    # Version active
    current_production_version: Optional[int] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifest":
        d = dict(data)
        if isinstance(d.get("environment"), dict):
            d["environment"] = ExecutionEnvironment.from_dict(d["environment"])
        if isinstance(d.get("checkpoints"), list):
            d["checkpoints"] = [Checkpoint.from_dict(cp) if isinstance(cp, dict) else cp for cp in d["checkpoints"]]
        return cls(**d)


@dataclass
class BreakoutReport:
    """
    Rapport structuré émis par l'hôte en cas d'interruption / d'ancre introuvable.
    Permet au Planner de reprendre la mission sans recommencer depuis le début.
    """
    skill_id: str
    version: int
    failed_checkpoint_id: str
    completed_checkpoints: List[str] = field(default_factory=list)
    failure_class: FailureClass = FailureClass.UNKNOWN
    error_message: str = ""
    observed_state_ref: Optional[str] = None
    recoverability: str = "MEDIUM"
    resume_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["failure_class"] = self.failure_class.value
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BreakoutReport":
        d = dict(data)
        if isinstance(d.get("failure_class"), str):
            d["failure_class"] = FailureClass(d["failure_class"])
        return cls(**d)


@dataclass
class FailureBundle:
    """Paquet complet d'incident sauvegardé pour le corpus de régression et l'auto-réparation."""
    incident_id: str
    mission_id: str
    skill_id: str
    version: int
    breakout_report: BreakoutReport
    timestamp: float = field(default_factory=time.time)
    parameters_used: Dict[str, Any] = field(default_factory=dict)
    host_environment_snapshot: Dict[str, Any] = field(default_factory=dict)
    trace_artifact_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FailureBundle":
        d = dict(data)
        if isinstance(d.get("breakout_report"), dict):
            d["breakout_report"] = BreakoutReport.from_dict(d["breakout_report"])
        return cls(**d)


@dataclass
class SkillPackage:
    """
    Package exportable/importable (.skillpkg / JSON bundle autonome).
    Permet la portabilité, le partage ou la vente de bibliothèques de compétences.
    """
    manifest: SkillManifest
    package_format_version: str = "1.0.0"
    versions: List[SkillVersion] = field(default_factory=list)
    embedded_payloads: Dict[str, str] = field(default_factory=dict)
    exported_at: float = field(default_factory=time.time)
    signature_checksum: Optional[str] = None

    def export_json(self) -> str:
        """Sérialise le package complet au format JSON."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def import_json(cls, json_str: str) -> "SkillPackage":
        """Désérialise et valide l'intégrité d'un package de Skill."""
        data = json.loads(json_str)
        manifest = SkillManifest.from_dict(data["manifest"])
        versions = [SkillVersion.from_dict(v) for v in data.get("versions", [])]
        return cls(
            manifest=manifest,
            package_format_version=data.get("package_format_version", "1.0.0"),
            versions=versions,
            embedded_payloads=data.get("embedded_payloads", {}),
            exported_at=data.get("exported_at", time.time()),
            signature_checksum=data.get("signature_checksum")
        )

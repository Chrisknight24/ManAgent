"""
Module core.skills.models
Définition des contrats et structures de données pour le cycle de vie des Skills ManAgent.
Garantit l'immutabilité, l'exportabilité (package .skillpkg / bundle JSON),
l'indexation par signatures de mission et le typage strict.
Compatible Python standard (dataclasses) pour une exécution ultra-rapide et zéro dépendance externe.
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, fields, asdict
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
    PRECONDITION_FAILED = "PRECONDITION_FAILED"      # Précondition obligatoire non satisfaite (ex: app fermée)
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"    # Postcondition non garantie à l'issue du flux
    SKILL_LOGIC_ERROR = "SKILL_LOGIC_ERROR"  # Erreur structurelle dans le graphe
    EXECUTION_ERROR = "EXECUTION_ERROR"      # Échec d'exécution d'un outil sous-jacent
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
    Spécification agnostique de l'environnement d'exécution requis par un Skill.
    ManAgent est 100% neutre et indépendant de l'hôte (qu'il s'agisse d'un PC client
    avec AutoCUse, d'un serveur Linux distant, d'une machine virtuelle, d'un conteneur
    headless ou d'un terminal mobile).
    
    L'hôte envoie son empreinte d'environnement (`host_env` sous forme de dictionnaire clé/valeur).
    ManAgent procède à un filtrage rigide, froid et déterministe entre les exigences
    déclarées dans `requirements` et les données fournies par l'hôte.
    """
    requirements: Dict[str, Any] = field(default_factory=dict)
    variant_tag: str = "DEFAULT"
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    postconditions: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        requirements: Optional[Dict[str, Any]] = None,
        variant_tag: str = "DEFAULT",
        preconditions: Optional[List[Dict[str, Any]]] = None,
        postconditions: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        self.requirements = dict(requirements or {})
        self.variant_tag = variant_tag
        self.preconditions = list(preconditions or [])
        self.postconditions = list(postconditions or [])
        self.metadata = dict(metadata or {})

        # Intégrer tous les paramètres supplémentaires (ex: os, os_family, display_scale_dpi, locale, etc.)
        # dans le dictionnaire de contraintes agnostique
        for k, v in kwargs.items():
            if v is not None:
                self.requirements[k] = v

    def __getattr__(self, item: str) -> Any:
        if "requirements" in self.__dict__ and item in self.requirements:
            return self.requirements[item]
        return None

    def calculate_compatibility(self, host_env: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
        """
        Évaluation déterministe, rigide et froide de l'empreinte hôte.
        ManAgent ne présuppose aucun OS ou matériel en dur : il compare froidement
        les contraintes déclarées par le Skill avec les caractéristiques réelles de l'hôte.

        Renvoie:
            - is_match (bool): True si AUCUNE exigence requise n'est violée.
            - specificity_score (float): Nombre d'exigences satisfaites. Plus le skill est ciblé
              et correspond précisément à la machine de l'hôte, plus son score de spécificité est élevé.
            - mismatch_reasons (List[str]): Raisons explicites en cas de rejet.
        """
        if not host_env:
            if self.requirements:
                return False, 0.0, ["Aucune empreinte d'environnement hôte fournie alors que le skill exige des prérequis."]
            return True, 0.0, []

        merged_host: Dict[str, Any] = dict(host_env)
        if isinstance(host_env.get("metadata"), dict):
            for mk, mv in host_env["metadata"].items():
                if mk not in merged_host:
                    merged_host[mk] = mv

        mismatches: List[str] = []
        specificity: float = 0.0

        for req_key, req_val in self.requirements.items():
            if req_val is None or req_val == "" or req_val == "any":
                continue

            # Recherche directe ou avec dérivation de préfixes génériques (target_, required_, min_, max_)
            host_val = merged_host.get(req_key)
            if host_val is None:
                for prefix in ("target_", "required_host_", "required_", "min_", "max_"):
                    if req_key.startswith(prefix):
                        short_key = req_key[len(prefix):]
                        if short_key in merged_host:
                            host_val = merged_host[short_key]
                            break

            # RÈGLE AGNOSTIQUE : Si l'hôte n'a pas fourni cette clé d'environnement,
            # elle ne bloque pas l'exécution (tolérance agnostique), mais ne donne pas de bonus de spécificité.
            if host_val is None:
                continue

            # 1. Vérification de sous-ensemble de capacités ou listes
            if isinstance(req_val, (list, set)):
                if isinstance(host_val, (list, set)):
                    host_items = set(str(c).lower().strip() for c in host_val)
                    missing = [c for c in req_val if str(c).lower().strip() not in host_items]
                    if missing:
                        mismatches.append(f"Éléments hôte requis manquants pour '{req_key}': {missing}")
                    else:
                        specificity += float(len(req_val))
                else:
                    norm_host = str(host_val).lower().strip()
                    norm_list = [str(x).lower().strip() for x in req_val]
                    if norm_host not in norm_list and "any" not in norm_list:
                        mismatches.append(f"Exigence '{req_key}' non satisfaite: hôte='{host_val}', autorisés={req_val}")
                    else:
                        specificity += 1.0

            # 2. Booléen
            elif isinstance(req_val, bool):
                if bool(host_val) != req_val:
                    mismatches.append(f"Exigence booléenne '{req_key}' non satisfaite: hôte={host_val}, requis={req_val}")
                else:
                    specificity += 1.0

            # 4. Numérique (avec support min_ / _min et max_ / _max)
            elif isinstance(req_val, (int, float)):
                try:
                    num_host = float(host_val)
                    num_req = float(req_val)
                    if req_key.endswith("_min") or req_key.startswith("min_"):
                        if num_host < num_req:
                            mismatches.append(f"Exigence minimale '{req_key}' non satisfaite: hôte={host_val} < requis={req_val}")
                        else:
                            specificity += 1.0
                    elif req_key.endswith("_max") or req_key.startswith("max_"):
                        if num_host > num_req:
                            mismatches.append(f"Exigence maximale '{req_key}' non satisfaite: hôte={host_val} > requis={req_val}")
                        else:
                            specificity += 1.0
                    else:
                        if num_host != num_req:
                            mismatches.append(f"Exigence numérique '{req_key}' non satisfaite: hôte={host_val} != requis={req_val}")
                        else:
                            specificity += 1.0
                except (ValueError, TypeError):
                    if str(host_val) != str(req_val):
                        mismatches.append(f"Exigence '{req_key}' non satisfaite: hôte={host_val}, requis={req_val}")
                    else:
                        specificity += 1.0

            # 4. Chaîne ou objet
            else:
                norm_req = str(req_val).strip().lower()
                norm_host = str(host_val).strip().lower()
                if norm_req != norm_host:
                    # Tolérance de locale linguistique (ex: 'fr' matche 'fr_FR')
                    if req_key in ("locale", "language") and (norm_host.startswith(norm_req) or norm_req.startswith(norm_host)):
                        specificity += 1.0
                    else:
                        mismatches.append(f"Exigence '{req_key}' non satisfaite: hôte='{host_val}', requis='{req_val}'")
                else:
                    specificity += 1.0

        is_match = (len(mismatches) == 0)
        return is_match, specificity, mismatches

    def is_compatible(self, host_env: Dict[str, Any]) -> bool:
        """Vérifie de manière déterministe si l'environnement de l'hôte satisfait les exigences."""
        is_match, _, _ = self.calculate_compatibility(host_env)
        return is_match

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "requirements": self.requirements,
            "variant_tag": self.variant_tag,
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "metadata": self.metadata,
        }
        for k, v in self.requirements.items():
            if k not in d:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEnvironment":
        if not isinstance(data, dict):
            return cls()
        known = {"requirements", "variant_tag", "preconditions", "postconditions", "metadata"}
        reqs = dict(data.get("requirements") or {})
        for k, v in data.items():
            if k not in known and v is not None:
                reqs[k] = v
        return cls(
            requirements=reqs,
            variant_tag=data.get("variant_tag", "DEFAULT"),
            preconditions=data.get("preconditions", []),
            postconditions=data.get("postconditions", []),
            metadata=data.get("metadata", {})
        )


@dataclass
class TrustProfile:
    """Profil statistique et métriques de fiabilité d'une version de Skill."""
    success_count: int = 0
    failure_count: int = 0
    breakout_count: int = 0
    consecutive_failures: int = 0
    shadow_validation_count: int = 0
    shadow_mismatch_count: int = 0
    distinct_context_count: int = 0
    last_success_timestamp: Optional[float] = None
    last_failure_timestamp: Optional[float] = None
    recent_execution_window: List[bool] = field(default_factory=list)

    def record_run(self, success: bool, is_breakout: bool = False, is_shadow: bool = False, context_id: Optional[str] = None, shadow_mismatch: bool = False):
        """Enregistre le résultat d'une exécution et met à jour les indicateurs."""
        now = time.time()
        if is_shadow:
            if shadow_mismatch:
                self.shadow_mismatch_count += 1
            elif success:
                self.shadow_validation_count += 1
                if self.shadow_mismatch_count > 0:
                    self.shadow_mismatch_count = max(0, self.shadow_mismatch_count - 1)
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
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in (data or {}).items() if k in valid_keys}
        return cls(**filtered)


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
    environment_variant_tag: Optional[str] = None
    
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
    
    # Contrat d'États pour le Planner HTN
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    postconditions: List[Dict[str, Any]] = field(default_factory=list)
    environment_variant_tag: Optional[str] = None
    
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
        
        # Synchronisation bidirectionnelle pré/postconditions si stockées dans environment
        if not d.get("preconditions") and getattr(d.get("environment"), "preconditions", None):
            d["preconditions"] = list(d["environment"].preconditions)
        if not d.get("postconditions") and getattr(d.get("environment"), "postconditions", None):
            d["postconditions"] = list(d["environment"].postconditions)

        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


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
    embedded_payloads: Dict[str, Any] = field(default_factory=dict)
    exported_at: float = field(default_factory=time.time)
    signature_checksum: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le package complet en dictionnaire sérialisable avec payloads déballés."""
        d = asdict(self)
        if isinstance(d.get("embedded_payloads"), dict):
            clean_payloads = {}
            for k, v in d["embedded_payloads"].items():
                if isinstance(v, str):
                    try:
                        clean_payloads[k] = json.loads(v)
                    except Exception:
                        clean_payloads[k] = v
                else:
                    clean_payloads[k] = v
            d["embedded_payloads"] = clean_payloads
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillPackage":
        """Reconstitue un SkillPackage depuis un dictionnaire."""
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

    def export_json(self) -> str:
        """Sérialise le package complet au format JSON."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def import_json(cls, json_str: str) -> "SkillPackage":
        """Désérialise et valide l'intégrité d'un package de Skill."""
        data = json.loads(json_str)
        return cls.from_dict(data)

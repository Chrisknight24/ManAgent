"""
core/host_manifest.py
====================
Contrats de données et validation pour le Host Manifest (déclaration dynamique de l'hôte).
ManAgent reste 100% générique et agnostique vis-à-vis de l'application hôte (ex: AutoCUse RPA ou autre).
L'hôte peut annoncer dynamiquement :
- Son OS et environnement d'exécution
- Ses outils matériels/externes
- Ses capacités d'exécution (ex: souris, clavier, uia, vision, flow_engine, etc.)
- Ses métadonnées spécifiques
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class HostManifest:
    """
    Manifeste dynamique fourni par l'hôte lors de l'initialisation ou reconfiguration.
    Permet à n'importe quelle application cliente (RPA, Robotique, Web, API, CLI, Cloud)
    d'annoncer ses outils, capacités et son environnement d'exécution de façon 100% agnostique.
    """
    host_name: str = "generic_host"
    host_version: str = "1.0.0"
    capabilities: List[str] = field(default_factory=list)  # Capacités brutes déclarées par l'hôte
    tools: List[Dict[str, Any]] = field(default_factory=list)  # Schémas des outils déclarés par l'hôte
    environment: Dict[str, Any] = field(default_factory=dict)  # Empreinte d'environnement brute
    metadata: Dict[str, Any] = field(default_factory=dict)     # Données libres supplémentaires

    def __init__(
        self,
        host_name: str = "generic_host",
        host_version: str = "1.0.0",
        capabilities: Optional[List[str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        environment: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        self.host_name = host_name
        self.host_version = host_version
        self.capabilities = list(capabilities or [])
        self.tools = list(tools or [])
        self.environment = dict(environment or {})
        self.metadata = dict(metadata or {})

        # Tout argument additionnel est intégré dynamiquement dans l'environnement de l'hôte
        for k, v in kwargs.items():
            if v is not None:
                self.environment[k] = v

    def __getattr__(self, item: str) -> Any:
        if "environment" in self.__dict__ and item in self.environment:
            return self.environment[item]
        if "metadata" in self.__dict__ and item in self.metadata:
            return self.metadata[item]
        return None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "host_name": self.host_name,
            "host_version": self.host_version,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "environment": self.environment,
            "metadata": self.metadata,
        }
        for k, v in self.environment.items():
            if k not in d:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HostManifest":
        if not isinstance(data, dict):
            return cls()
        
        env = dict(data.get("environment") or {})
        meta = dict(data.get("metadata") or {})
        
        # Ingestion transparente de tout champ contextuel passé par l'hôte
        for k, v in data.items():
            if k not in ("host_name", "host_version", "capabilities", "tools", "environment", "metadata") and v is not None:
                env[k] = v

        return cls(
            host_name=data.get("host_name", "generic_host"),
            host_version=data.get("host_version", "1.0.0"),
            capabilities=data.get("capabilities", []),
            tools=data.get("tools", []),
            environment=env,
            metadata=meta
        )

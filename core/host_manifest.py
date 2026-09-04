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
    Permet à n'importe quelle application cliente d'annoncer ses outils et capacités
    de façon agnostique et transparente.
    """
    host_name: str = "generic_host"
    host_version: str = "1.0.0"
    os: str = "generic"  # ex: "windows", "linux", "macos"
    os_version: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)  # ex: ["mouse", "keyboard", "uia", "vision", "flo_runner"]
    resolution_width: Optional[int] = None
    resolution_height: Optional[int] = None
    dpi_scale: float = 1.0
    tools: List[Dict[str, Any]] = field(default_factory=list)  # Outils externes déclarés par l'hôte
    metadata: Dict[str, Any] = field(default_factory=dict)     # Données libres supplémentaires

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HostManifest":
        if not isinstance(data, dict):
            return cls()
        return cls(
            host_name=data.get("host_name", "generic_host"),
            host_version=data.get("host_version", "1.0.0"),
            os=data.get("os", "generic"),
            os_version=data.get("os_version"),
            capabilities=data.get("capabilities", []),
            resolution_width=data.get("resolution_width"),
            resolution_height=data.get("resolution_height"),
            dpi_scale=float(data.get("dpi_scale", 1.0)),
            tools=data.get("tools", []),
            metadata=data.get("metadata", {})
        )

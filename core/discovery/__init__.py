"""
core/discovery/__init__.py
==========================
Point d'entrée du package Discovery.
"""

from core.discovery.models import (
    DiscoveryPlan,
    DiscoveryStep,
    DiscoveryRequest,
    RefinedContext,
    WorkspaceEntry,
    ExitPolicy,
    StepType
)
from core.discovery.base_explorer import BaseExplorer
from core.discovery.discovery_session import DiscoverySession
from core.discovery.discovery_engine import DiscoveryEngine
from core.discovery.workspace import Workspace
from core.discovery.explorers import RegistryExplorer  # <-- NOUVEAU

__all__ = [
    "DiscoveryPlan",
    "DiscoveryStep",
    "DiscoveryRequest",
    "RefinedContext",
    "WorkspaceEntry",
    "ExitPolicy",
    "StepType",
    "BaseExplorer",
    "DiscoverySession",
    "DiscoveryEngine",
    "Workspace",
    "RegistryExplorer"  # <-- NOUVEAU
]
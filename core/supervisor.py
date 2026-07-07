# Dans supervisor.py
from abc import ABC, abstractmethod
from .plan_models import Plan

class Supervisor(ABC):

    @abstractmethod
    async def validate_plan(self, plan: Plan, child_solver_id: str) -> bool:
        pass

    @abstractmethod
    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        pass

    # NOUVEAU CONTRAT
    @abstractmethod
    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Transmet ou exécute une action matérielle."""
        pass

    @abstractmethod
    async def propagate_event(self, event_name: str, payload: dict):
        """Méthode générique pour remonter un événement vers le Hub (Orchestrator)."""
        pass
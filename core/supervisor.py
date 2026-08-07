# core/supervisor.py
from abc import ABC, abstractmethod
from .plan_models import Plan
from core.llm import Llm
from typing import Optional

class Supervisor(ABC):

    @abstractmethod
    async def validate_plan(self, plan: Plan, child_solver_id: str) -> bool:
        pass

    @abstractmethod
    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        pass

    @abstractmethod
    async def execute_tool(self, tool_name: str, arguments: dict, llm: Optional[Llm] = None) -> str:
        """Transmet ou exécute une action matérielle. Le LLM est optionnel."""
        pass

    @abstractmethod
    async def propagate_event(self, event_name: str, payload: dict):
        pass
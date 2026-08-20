# core/supervisor.py
from abc import ABC, abstractmethod
from .plan_models import Plan
from core.execution_models import PlanAttempt
from core.llm import Llm
from typing import Optional, List, Dict, Any

class Supervisor(ABC):

    @abstractmethod
    async def validate_plan(
        self,
        plan: Plan,
        child_solver_id: str,
        previous_attempts: Optional[List[PlanAttempt]] = None,
        ancestor_chain: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Valide un plan avant exécution (le "LLM Judge", voir
        core/plan_validator.py). Retourne un PlanValidationOutcome (utilisable
        comme un bool pour compat : `if not outcome:`), qui porte aussi
        `reason`/`risk_level`/etc. — pas juste un bool nu comme auparavant,
        pour que l'appelant (Solver) puisse réinjecter une vraie justification
        dans le feedback au Planner plutôt qu'un message générique fixe.
        `ancestor_chain` : chaîne racine -> Solver courant ({depth, goal,
        solver_id} par niveau), pour détecter une récursion inter-niveaux
        invisible à la seule comparaison avec previous_attempts.
        """
        pass

    @abstractmethod
    async def request_depth_extension(
        self,
        ancestor_chain: List[Dict[str, Any]],
        child_solver_id: str,
    ) -> bool:
        """
        Demande une extension de la profondeur maximale de récursion pour une
        chaîne de sous-tâches (abstract_task imbriqués) qui vient d'atteindre
        MAX_DEPTH. `ancestor_chain` est la liste ordonnée (racine -> courant)
        de {depth, goal, solver_id} de chaque niveau. Retourne True si
        l'extension est accordée (le juge estime qu'il s'agit d'une
        décomposition légitime, ET le plafond absolu d'extensions n'est pas
        déjà atteint pour cette mission), False sinon.
        """
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

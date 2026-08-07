"""
core/discovery/workspace.py
===========================
Gestionnaire du Workspace (mémoire temporaire).
"""

from typing import List, Optional, Dict, Any
from core.discovery.models import WorkspaceEntry, RefinedContext, ExitPolicy
from utils.logger import Logger
import json

class Workspace:
    """
    Mémoire temporaire d'une DiscoverySession.
    Contient les questions, réponses, observations et conclusions.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._entries: List[WorkspaceEntry] = []
        self._summary: Optional[str] = None
        self._exit_policy: Optional[ExitPolicy] = None

    def add_entry(
        self,
        step_id: str,
        question: str,
        answer: Any,  # <-- changer le type en Any
        tool_name: Optional[str] = None,
        tool_args_raw: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None
    ) -> None:
        # Convertir answer en str si nécessaire
        if not isinstance(answer, str):
            try:
                answer = json.dumps(answer, ensure_ascii=False, default=str)
            except Exception:
                answer = str(answer)
        entry = WorkspaceEntry(
            step_id=step_id,
            question=question,
            answer=answer,
            tool_name=tool_name,
            tool_args_raw=tool_args_raw,
            tool_result=tool_result
        )
        self._entries.append(entry)
        Logger.debug(f"[Workspace:{self.session_id}] Entrée ajoutée : {step_id}")
        
    def get_entries(self) -> List[WorkspaceEntry]:
        """Retourne toutes les entrées."""
        return self._entries.copy()

    def get_last_entry(self) -> Optional[WorkspaceEntry]:
        """Retourne la dernière entrée."""
        return self._entries[-1] if self._entries else None

    def set_summary(self, summary: str) -> None:
        """Définit le résumé final."""
        self._summary = summary

    def get_summary(self) -> Optional[str]:
        """Retourne le résumé final."""
        return self._summary

    def set_exit_policy(self, policy: ExitPolicy) -> None:
        """Définit la politique de sortie."""
        self._exit_policy = policy

    def get_exit_policy(self) -> Optional[ExitPolicy]:
        """Retourne la politique de sortie."""
        return self._exit_policy

    def to_refined_context(
        self,
        signature: str,
        data_type: str,
        target: str,
        goal: str,
        technical_goal: str  # <-- NOUVEAU
    ) -> RefinedContext:
        """
        Produit un RefinedContext à partir du Workspace.
        """
        if self._summary is None:
            self._summary = self._generate_summary()

        return RefinedContext(
            signature=signature,
            data_type=data_type,
            target=target,
            goal=goal,
            technical_goal=technical_goal,  # <-- NOUVEAU
            entries=self._entries.copy(),
            summary=self._summary,
            exit_policy=self._exit_policy or ExitPolicy.PLAN_COMPLETED
        )

    def _generate_summary(self) -> str:
        """Génère un résumé des entrées du Workspace."""
        if not self._entries:
            return "Aucune information collectée."

        lines = []
        for entry in self._entries:
            lines.append(f"- {entry.question} : {entry.answer}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Vide le Workspace."""
        self._entries.clear()
        self._summary = None
        self._exit_policy = None
        Logger.debug(f"[Workspace:{self.session_id}] Workspace vidé.")

    def is_empty(self) -> bool:
        """Vérifie si le Workspace est vide."""
        return len(self._entries) == 0
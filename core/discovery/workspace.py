"""
core/discovery/workspace.py
===========================
Gestionnaire du Workspace (mémoire temporaire).
"""

from typing import List, Optional, Dict, Any
from core.discovery.models import WorkspaceEntry, RefinedContext, ExitPolicy
from utils.logger import Logger
import json

# Certains outils (ex. get_mission_details, inspect_value) renvoient des
# données brutes non condensées (arbre d'exécution complet, dump de
# registre...). Sans limite, ces réponses se propagent telles quelles dans
# insights_by_mission (session_memory), dans le prompt de l'étape sémantique
# suivante, puis dans le RefinedContext.summary injecté directement dans le
# prompt de l'Orchestrateur — c'est la source du "gaspillage de tokens".
# On borne donc la taille stockée ICI, à la source, pour protéger tous les
# consommateurs en aval en un seul endroit.
MAX_ENTRY_ANSWER_CHARS = 4000


def _cap_answer(answer: str, max_chars: int = MAX_ENTRY_ANSWER_CHARS) -> str:
    if len(answer) <= max_chars:
        return answer
    total = len(answer)
    return answer[:max_chars] + f"\n... [tronqué, {total} caractères au total]"


class Workspace:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._entries: List[WorkspaceEntry] = []
        self._summary: Optional[str] = None
        self._exit_policy: Optional[ExitPolicy] = None

    def add_entry(
        self,
        step_id: str,
        question: str,
        answer: Any,
        tool_name: Optional[str] = None,
        tool_args_raw: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        max_answer_chars: int = MAX_ENTRY_ANSWER_CHARS,
    ) -> None:
        if not isinstance(answer, str):
            try:
                answer = json.dumps(answer, ensure_ascii=False, default=str)
            except Exception:
                answer = str(answer)
        answer = _cap_answer(answer, max_answer_chars)
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
        return self._entries.copy()

    def get_last_entry(self) -> Optional[WorkspaceEntry]:
        return self._entries[-1] if self._entries else None

    def set_summary(self, summary: str) -> None:
        self._summary = summary

    def get_summary(self) -> Optional[str]:
        return self._summary

    def set_exit_policy(self, policy: ExitPolicy) -> None:
        self._exit_policy = policy

    def get_exit_policy(self) -> Optional[ExitPolicy]:
        return self._exit_policy

    def to_refined_context(
        self,
        signature: str,
        data_type: str,
        targets: List[str],
        technical_goals: List[str],
        goal: str,
    ) -> RefinedContext:
        if self._summary is None:
            self._summary = self._generate_summary()
        return RefinedContext(
            signature=signature,
            data_type=data_type,
            targets=targets,
            technical_goals=technical_goals,
            goal=goal,
            entries=self._entries.copy(),
            summary=self._summary,
            exit_policy=self._exit_policy or ExitPolicy.PLAN_COMPLETED
        )

    def _generate_summary(self) -> str:
        if not self._entries:
            return "Aucune information collectée."
        lines = []
        for entry in self._entries:
            lines.append(f"- {entry.question} : {entry.answer}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._entries.clear()
        self._summary = None
        self._exit_policy = None
        Logger.debug(f"[Workspace:{self.session_id}] Workspace vidé.")

    def is_empty(self) -> bool:
        return len(self._entries) == 0
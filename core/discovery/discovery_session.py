"""
core/discovery/discovery_session.py
===================================
Gestionnaire de la DiscoverySession.
Version avec support du data_context (partage implicite).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Dict, Any
from core.discovery.models import (
    DiscoveryPlan,
    DiscoverySessionState,
    DiscoveryStep,
    StepType,
    ExitPolicy,
    RefinedContext
)
from core.discovery.workspace import Workspace
from core.discovery.base_explorer import BaseExplorer
from core.runtime_state import RuntimeState
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from core.constants import DISCOVERY_MAX_ITERATIONS, Events
from core.i18n import _
from utils.logger import Logger


class DiscoverySession:
    """
    Représente une investigation temporaire.
    """

    def __init__(
        self,
        entity_id: str,
        plan: DiscoveryPlan,
        explorer: BaseExplorer,
        runtime_state: RuntimeState,
        llm: Optional[Llm] = None,
        max_iterations: int = DISCOVERY_MAX_ITERATIONS
    ):
        self.session_id = plan.signature or f"ds_{entity_id[:8]}_{int(time.time())}"
        self.entity_id = entity_id
        self.plan = plan
        self.explorer = explorer
        self.runtime_state = runtime_state
        self.max_iterations = max_iterations

        # Déterminer le LLM pour les étapes sémantiques
        self._llm = llm or getattr(runtime_state, "discovery_llm", None)
        if not self._llm:
            Logger.warning(
                f"[DiscoverySession:{self.session_id}] {_('Aucun LLM fourni pour les étapes sémantiques.')}"
            )

        self.workspace = Workspace(self.session_id)
        self.state = DiscoverySessionState(
            session_id=self.session_id,
            entity_id=entity_id,
            plan=plan
        )
        self._started_at = time.time()
        self._is_running = False
        self._prompt_loader = get_prompt_loader()

        # --- NOUVEAU : stockage du contexte de données ---
        self._data_context: Optional[Any] = None

    async def run(self, data_context: Optional[Any] = None) -> RefinedContext:
        """Exécute la session et retourne un RefinedContext."""
        if self._is_running:
            raise RuntimeError(f"DiscoverySession {self.session_id} {_('déjà en cours.')}")

        # --- Définir le contexte de données ---
        if data_context is not None:
            self._data_context = data_context
        elif self._llm and hasattr(self._llm, 'get_data_context'):
            self._data_context = self._llm.get_data_context()
        # Sinon, on laisse None

        self._is_running = True
        Logger.info(f"[DiscoverySession:{self.session_id}] {_('Démarrage de la session.')}")

        await self._emit_event(Events.DISCOVERY_SESSION_START, {
            "goal": self.plan.goal,
            "data_type": self.plan.data_type,
            "target": self.plan.target,
            "max_iterations": self.max_iterations
        })

        try:
            for idx, step in enumerate(self.plan.steps):
                if idx >= self.max_iterations:
                    self.workspace.set_exit_policy(ExitPolicy.MAX_ITERATIONS)
                    Logger.warning(
                        f"[DiscoverySession:{self.session_id}] {_('Max itérations atteint')} ({self.max_iterations})."
                    )
                    break

                await self._execute_step(step, idx)

                if self.workspace.get_exit_policy() is not None:
                    break

            if self.workspace.get_exit_policy() is None:
                self.workspace.set_exit_policy(ExitPolicy.PLAN_COMPLETED)

            self.workspace.set_summary(self._generate_summary_from_workspace())

            refined = self.workspace.to_refined_context(
                signature=self.plan.signature or self.explorer.create_signature(
                    self.plan.technical_goal, self.plan.target
                ),
                data_type=self.plan.data_type,
                target=self.plan.target,
                goal=self.plan.goal,
                technical_goal=self.plan.technical_goal
            )

            Logger.info(
                f"[DiscoverySession:{self.session_id}] {_('Session terminée.')} "
                f"{_('ExitPolicy')}: {refined.exit_policy.value}"
            )

            await self._emit_event(Events.DISCOVERY_SESSION_END, {
                "exit_policy": refined.exit_policy.value,
                "entries_count": len(refined.entries),
                "summary": refined.summary
            })

            return refined

        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] {_('Erreur critique')}: {e}")
            self.workspace.set_exit_policy(ExitPolicy.TOOL_FAILED)
            refined = self.workspace.to_refined_context(
                signature=self.plan.signature or self.explorer.create_signature(
                    self.plan.technical_goal, self.plan.target
                ),
                data_type=self.plan.data_type,
                target=self.plan.target,
                goal=self.plan.goal,
                technical_goal=self.plan.technical_goal
            )
            refined.summary = f"{_('Erreur lors de la découverte')}: {str(e)}"
            await self._emit_event(Events.DISCOVERY_SESSION_END, {
                "exit_policy": ExitPolicy.TOOL_FAILED.value,
                "error": str(e)
            })
            return refined

        finally:
            self._is_running = False

    async def _execute_step(self, step: DiscoveryStep, index: int) -> None:
        Logger.debug(f"[DiscoverySession:{self.session_id}] {_('Exécution étape')} {index+1}: {step.description}")

        try:
            if step.type == StepType.TOOL:
                result, tool_args_raw = await self._execute_tool_step(step)
            elif step.type == StepType.SEMANTIC:
                result = await self._execute_semantic_step(step)
                tool_args_raw = None
            else:
                raise ValueError(f"{_('Type d\'étape inconnu')}: {step.type}")

            self.workspace.add_entry(
                step_id=step.id,
                question=step.description,
                answer=result.get("data", ""),
                tool_name=step.tool_name,
                tool_args_raw=tool_args_raw,
                tool_result=result
            )

            if not self._check_expected_result(step, result):
                self.workspace.set_exit_policy(ExitPolicy.EXPECTED_RESULT_FOUND)
                Logger.debug(
                    f"[DiscoverySession:{self.session_id}] {_('Expected_result non satisfait')} "
                    f"({_('attendu')}: {step.expected_result}, {_('obtenu')}: {result.get('success')})."
                )

            await self._emit_event(Events.DISCOVERY_STEP, {
                "step_id": step.id,
                "step_index": index,
                "step_type": step.type.value,
                "result": result
            })

        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] {_('Échec de l\'étape')} {step.id}: {e}")
            self.workspace.set_exit_policy(ExitPolicy.TOOL_FAILED)
            raise

    async def _execute_tool_step(self, step: DiscoveryStep) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        if not step.tool_name:
            raise ValueError(_("Étape TOOL sans tool_name."))

        tools = self.explorer.get_tools_description()
        tool_names = [t["name"] for t in tools]
        if step.tool_name not in tool_names:
            raise ValueError(
                _("Outil '{tool_name}' non supporté par l'Explorer.").format(tool_name=step.tool_name)
            )

        tool_args_raw = step.tool_args.copy() if step.tool_args else None
        # On n'a pas besoin de passer data_context ici car l'explorer peut le récupérer via sa méthode
        result = await self.explorer.execute_tool(step.tool_name, step.tool_args)

        if "success" not in result:
            result["success"] = False
            result["data"] = _("Résultat invalide de l'outil.")

        return result, tool_args_raw

    async def _execute_semantic_step(self, step: DiscoveryStep) -> Dict[str, Any]:
        if not step.question:
            raise ValueError(_("Étape SEMANTIC sans question."))

        if not self._llm:
            return {
                "success": False,
                "data": _("Aucun LLM disponible pour les étapes sémantiques.")
            }

        try:
            prompt = self._prompt_loader.load(
                "discovery_semantic.md",
                lang=getattr(self.runtime_state, "language", "en"),
                data_type=self.plan.data_type,
                target=self.plan.target,
                question=step.question
            )
        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] {_('Erreur chargement prompt')}: {e}")
            prompt = _(
                "Contexte : Exploration du type de données '{data_type}' sur la cible '{target}'.\n"
                "Question : {question}\n"
                "Réponds de manière concise et factuelle."
            ).format(
                data_type=self.plan.data_type,
                target=self.plan.target,
                question=step.question
            )

        try:
            response = await self._llm.generate_text(prompt, tag="discovery_semantic")
            return {"success": True, "data": response}
        except Exception as e:
            return {"success": False, "data": f"{_('Erreur LLM')}: {str(e)}"}

    def _check_expected_result(self, step: DiscoveryStep, result: Dict[str, Any]) -> bool:
        expected = step.expected_result.strip().lower()
        if expected == "any":
            return True
        success = result.get("success", False)
        actual = "true" if success else "false"
        return expected == actual

    def _generate_summary_from_workspace(self) -> str:
        entries = self.workspace.get_entries()
        if not entries:
            return _("Impossible de répondre à l'objectif '{goal}'.").format(goal=self.plan.goal)
        lines = [f"- {entry.question} → {entry.answer}" for entry in entries]
        return "\n".join(lines)

    async def _emit_event(self, event_name: str, payload: dict):
        exec_ctx = getattr(self.runtime_state, 'execution_context', {})
        payload["session_id"] = self.session_id
        payload["entity_id"] = self.entity_id
        payload["entity_type"] = getattr(self.runtime_state, "current_entity_type", "unknown")
        payload["solver_id"] = exec_ctx.get("solver_id")
        payload["attempt_number"] = exec_ctx.get("attempt_number")
        payload["step_id"] = exec_ctx.get("step_id")
        payload["mission_id"] = self.runtime_state.current_mission_id
        Logger.event(event_name, **payload)
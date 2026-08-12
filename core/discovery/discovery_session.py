"""
core/discovery/discovery_session.py
===================================
Gestionnaire de la DiscoverySession.
Version avec support du data_context (partage implicite).
Support multi‑cibles : signature canonique basée sur toutes les cibles et goals.
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

        self._llm = llm or getattr(runtime_state, "discovery_llm", None)
        if not self._llm:
            Logger.warning(f"[DiscoverySession:{self.session_id}] Aucun LLM fourni pour les étapes sémantiques.")

        self.workspace = Workspace(self.session_id)
        self.state = DiscoverySessionState(
            session_id=self.session_id,
            entity_id=entity_id,
            plan=plan
        )
        self._started_at = time.time()
        self._is_running = False
        self._prompt_loader = get_prompt_loader()
        self._data_context: Optional[Any] = None

    def _build_signature_from_plan(self) -> str:
        """Construit une signature canonique à partir des cibles et goals du plan."""
        if not self.plan.targets or not self.plan.technical_goals:
            return "unknown"
        if len(self.plan.targets) == 1:
            return f"{self.plan.data_type}://{self.plan.targets[0]}/{self.plan.technical_goals[0]}"
        targets_str = "_".join(self.plan.targets)
        goals_str = "_".join(self.plan.technical_goals)
        return f"{self.plan.data_type}://multi/{targets_str}/{goals_str}"

    async def run(self, data_context: Optional[Any] = None) -> RefinedContext:
        if self._is_running:
            raise RuntimeError(f"DiscoverySession {self.session_id} déjà en cours.")

        if data_context is not None:
            self._data_context = data_context
        elif self._llm and hasattr(self._llm, 'get_data_context'):
            self._data_context = self._llm.get_data_context()

        self._is_running = True
        Logger.info(f"[DiscoverySession:{self.session_id}] Démarrage de la session.")

        await self._emit_event(Events.DISCOVERY_SESSION_START, {
            "goal": self.plan.goal,
            "data_type": self.plan.data_type,
            "targets": self.plan.targets,
            "technical_goals": self.plan.technical_goals,
            "max_iterations": self.max_iterations
        })

        try:
            for idx, step in enumerate(self.plan.steps):
                if idx >= self.max_iterations:
                    self.workspace.set_exit_policy(ExitPolicy.MAX_ITERATIONS)
                    Logger.warning(f"[DiscoverySession:{self.session_id}] Max itérations atteint ({self.max_iterations}).")
                    break

                await self._execute_step(step, idx)

                if self.workspace.get_exit_policy() is not None:
                    break

            if self.workspace.get_exit_policy() is None:
                self.workspace.set_exit_policy(ExitPolicy.PLAN_COMPLETED)

            self.workspace.set_summary(self._generate_summary_from_workspace())

            refined = self.workspace.to_refined_context(
                signature=self.plan.signature or self._build_signature_from_plan(),
                data_type=self.plan.data_type,
                targets=self.plan.targets,
                technical_goals=self.plan.technical_goals,
                goal=self.plan.goal
            )

            Logger.info(f"[DiscoverySession:{self.session_id}] Session terminée. ExitPolicy: {refined.exit_policy.value}")
            await self._emit_event(Events.DISCOVERY_SESSION_END, {
                "exit_policy": refined.exit_policy.value,
                "entries_count": len(refined.entries),
                "summary": refined.summary
            })

            return refined

        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] Erreur critique : {e}")
            self.workspace.set_exit_policy(ExitPolicy.TOOL_FAILED)
            refined = self.workspace.to_refined_context(
                signature=self.plan.signature or self._build_signature_from_plan(),
                data_type=self.plan.data_type,
                targets=self.plan.targets,
                technical_goals=self.plan.technical_goals,
                goal=self.plan.goal
            )
            refined.summary = f"Erreur lors de la découverte : {str(e)}"
            await self._emit_event(Events.DISCOVERY_SESSION_END, {
                "exit_policy": ExitPolicy.TOOL_FAILED.value,
                "error": str(e)
            })
            return refined
        finally:
            self._is_running = False

    async def _execute_step(self, step: DiscoveryStep, index: int) -> None:
        Logger.debug(f"[DiscoverySession:{self.session_id}] Exécution étape {index+1}: {step.description}")
        try:
            if step.type == StepType.TOOL:
                result, tool_args_raw = await self._execute_tool_step(step)
            elif step.type == StepType.SEMANTIC:
                result = await self._execute_semantic_step(step)
                tool_args_raw = None
            else:
                raise ValueError(f"Type d'étape inconnu : {step.type}")

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
                Logger.debug(f"[DiscoverySession:{self.session_id}] Expected_result non satisfait (attendu: {step.expected_result}, obtenu: {result.get('success')}).")

            await self._emit_event(Events.DISCOVERY_STEP, {
                "step_id": step.id,
                "step_index": index,
                "step_type": step.type.value,
                "result": result
            })

        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] Échec de l'étape {step.id}: {e}")
            self.workspace.set_exit_policy(ExitPolicy.TOOL_FAILED)
            raise

    async def _execute_tool_step(self, step: DiscoveryStep) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        if not step.tool_name:
            raise ValueError("Étape TOOL sans tool_name.")
        tools = self.explorer.get_tools_description()
        tool_names = [t["name"] for t in tools]
        if step.tool_name not in tool_names:
            raise ValueError(f"Outil '{step.tool_name}' non supporté par l'Explorer.")
        tool_args_raw = step.tool_args.copy() if step.tool_args else None
        result = await self.explorer.execute_tool(step.tool_name, step.tool_args)
        if "success" not in result:
            result["success"] = False
            result["data"] = "Résultat invalide de l'outil."
        return result, tool_args_raw

    async def _execute_semantic_step(self, step: DiscoveryStep) -> Dict[str, Any]:
        if not step.question:
            raise ValueError("Étape SEMANTIC sans question.")
        if not self._llm:
            return {"success": False, "data": "Aucun LLM disponible pour les étapes sémantiques."}
        try:
            targets_str = ", ".join(self.plan.targets) if self.plan.targets else "inconnue"
            prompt = self._prompt_loader.load(
                "discovery_semantic.md",
                lang=getattr(self.runtime_state, "language", "en"),
                data_type=self.plan.data_type,
                target=targets_str,
                question=step.question
            )
        except Exception as e:
            Logger.error(f"[DiscoverySession:{self.session_id}] Erreur chargement prompt : {e}")
            targets_str = ", ".join(self.plan.targets) if self.plan.targets else "inconnue"
            prompt = f"Contexte : Exploration du type de données '{self.plan.data_type}' sur les cibles {targets_str}.\nQuestion : {step.question}\nRéponds de manière concise et factuelle."
        try:
            response = await self._llm.generate_text(prompt, tag="discovery_semantic")
            return {"success": True, "data": response}
        except Exception as e:
            return {"success": False, "data": f"Erreur LLM : {str(e)}"}

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
            return f"Impossible de répondre à l'objectif '{self.plan.goal}'."
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
"""
core/discovery/explorers/history_explorer.py
======================================================
Explorer pour interroger l'historique conversationnel de la session.
Permet d'extraire des verbatim de tours, de rechercher des mots-clés ou de relire les derniers échanges.
"""

import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from core.discovery.base_explorer import BaseExplorer
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType, ExplorerStep
from core.runtime_state import RuntimeState
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.constants import Events
from utils.logger import Logger
from core.discovery.data_provider import DataProvider


class ExplorerPlanOutput(BaseModel):
    """
    Structure de réponse attendue du LLM de l'Explorer.
    """
    steps: List[ExplorerStep] = Field(
        ...,
        description=_("Liste des étapes à exécuter pour atteindre l'objectif.")
    )


class HistoryExplorer(BaseExplorer):
    """
    Explorer pour l'historique de chat de la session courante.
    """

    def __init__(self, runtime_state: RuntimeState, entity=None, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "history"
        self.entity = entity
        self.llm = llm
        self._prompt_loader = get_prompt_loader()

    def get_data_type(self) -> str:
        return self._data_type

    def get_scope_description(self) -> str:
        return "Consulte l'historique conversationnel textuel et les échanges de messages des tours précédents."

    def get_available_goals(self) -> List[str]:
        return [
            "get_recent_history",
            "get_history_by_turns",
            "search_history",
            "list_history",
            "get_history"
        ]

    def get_non_cacheable_goals(self) -> List[str]:
        """Les recherches sémantiques libres ne sont pas cacheables."""
        return ["search_history"]

    def _normalize_technical_goal(self, tg: str) -> str:
        tg_clean = tg.strip().lower()
        if tg_clean in ("list_history", "get_history", "recent", "recent_history"):
            return "get_recent_history"
        if tg_clean in ("by_turns", "history_by_turns", "turns"):
            return "get_history_by_turns"
        if tg_clean in ("search", "find_history"):
            return "search_history"
        return tg

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_recent_history",
                "description": _(
                    "Retourne les 10 derniers messages échangés de la session."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "get_history_by_turns",
                "description": _(
                    "Retourne l'intégralité d'une tranche d'échanges spécifiée par les tours. "
                    "Paramètre requis : 'target' (ex: 'turns_1_4' ou 'turn_3')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "La tranche de tours (ex: 'turns_1_4' ou 'turn_3')"
                        }
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "search_history",
                "description": _(
                    "Recherche par mot-clé parmi les messages de l'historique. "
                    "Paramètre requis : 'keyword'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "Le mot-clé à rechercher"
                        }
                    },
                    "required": ["keyword"]
                }
            }
        ]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            provider = None
            if self.entity and hasattr(self.entity, "get_data_provider"):
                provider = self.entity.get_data_provider("history")

            if tool_name == "get_recent_history":
                return await self._get_history(provider, "recent")
            elif tool_name == "get_history_by_turns":
                target = args.get("target", "recent")
                return await self._get_history(provider, target)
            elif tool_name == "search_history":
                keyword = args.get("keyword", "")
                return await self._get_history(provider, f"search:{keyword}")
            else:
                return {"success": False, "data": None, "message": _("Outil inconnu : {}").format(tool_name)}
        except Exception as e:
            Logger.error(f"[HistoryExplorer] Erreur lors de l'exécution de '{tool_name}' : {e}")
            return {"success": False, "data": None, "message": str(e)}

    async def _get_history(self, provider, target: str) -> Dict[str, Any]:
        if not provider:
            return {"success": False, "data": None, "message": _("Aucun DataProvider 'history' disponible.")}

        asset = provider.get_asset(target)
        if not asset:
            return {"success": False, "data": None, "message": f"Cible '{target}' non trouvée."}

        # dump_data ou renvoyer les données structurées
        text_verbatim = asset.dump_data()
        return {
            "success": True,
            "data": {
                "target": target,
                "description": asset.metadata.get("description", ""),
                "verbatim": text_verbatim,
                "messages": asset.data.get("messages", [])
            }
        }

    async def generate_plan(
        self,
        goal: str,
        technical_goal: Optional[str] = None,
        target: Optional[str] = None,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None,
        targets: Optional[List[str]] = None,
        technical_goals: Optional[List[str]] = None,
    ) -> DiscoveryPlan:
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("HistoryExplorer n'a pas de LLM pour générer un plan."))

        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets:
            targets = ["recent"]
        if not technical_goals:
            technical_goals = ["get_recent_history"]

        # Normaliser les goals techniques
        technical_goals = [self._normalize_technical_goal(tg) for tg in technical_goals]

        # Ajuster les longueurs si nécessaire
        if len(targets) != len(technical_goals):
            if len(technical_goals) == 1:
                technical_goals = technical_goals * len(targets)
            elif len(targets) == 1:
                targets = targets * len(technical_goals)
            else:
                min_len = min(len(targets), len(technical_goals))
                targets = targets[:min_len]
                technical_goals = technical_goals[:min_len]

        available_goals = self.get_available_goals()
        for tg in technical_goals:
            if tg not in available_goals:
                raise ValueError(
                    _("Le goal technique '{tg}' n'est pas supporté par HistoryExplorer. Goals disponibles : {goals}")
                    .format(tg=tg, goals=", ".join(available_goals))
                )

        if len(targets) == 1:
            signature = f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        else:
            targets_str = "_".join(targets)
            goals_str = "_".join(technical_goals)
            signature = f"{self._data_type}://multi/{targets_str}/{goals_str}"

        tools_desc = self.get_tools_description()
        tools_text = "\n".join([
            f"- **{t['name']}** : {t['description']} (paramètres : {t.get('parameters', {})})"
            for t in tools_desc
        ])

        prompt = self._prompt_loader.load(
            "explorer_plan_generation.md",
            lang=getattr(self.runtime_state, "language", "en"),
            goal=goal,
            targets=targets,
            technical_goals=technical_goals,
            data_type=self._data_type,
            tools_description=tools_text
        )

        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_START,
            data_type=self._data_type,
            targets=targets,
            technical_goals=technical_goals,
            goal=goal,
            signature=signature,
        )

        with self.runtime_state.execution_context.scope(discovery_signature=signature):
            try:
                result: ExplorerPlanOutput = await effective_llm.generate_structured(
                    prompt=prompt,
                    schema=ExplorerPlanOutput,
                    tag="explorer_plan_generation"
                )
                steps = result.steps
            except Exception as e:
                Logger.event(
                    Events.DISCOVERY_PLAN_GENERATION_ERROR,
                    data_type=self._data_type,
                    targets=targets,
                    technical_goals=technical_goals,
                    error=str(e),
                    signature=signature,
                )
                raise ValueError(
                    _("Échec de la génération du plan par le LLM de l'Explorer : {error}")
                    .format(error=e)
                )

        discovery_steps = []
        tool_names = [t["name"] for t in tools_desc]
        for idx, step in enumerate(steps):
            if step.type == "tool":
                if not step.tool_name:
                    raise ValueError(_("Étape {idx} de type 'tool' sans tool_name").format(idx=idx))
                if step.tool_name not in tool_names:
                    raise ValueError(
                        _("L'outil '{tool_name}' demandé n'existe pas pour HistoryExplorer. "
                          "Outils disponibles : {tools}")
                        .format(tool_name=step.tool_name, tools=", ".join(tool_names))
                    )
                try:
                    tool_args = json.loads(step.tool_args_json) if step.tool_args_json else {}
                except json.JSONDecodeError:
                    raise ValueError(_("tool_args_json invalide pour l'étape {idx}").format(idx=idx))

                discovery_steps.append(
                    DiscoveryStep(
                        id=f"step_{idx}",
                        type=StepType.TOOL,
                        description=step.description,
                        tool_name=step.tool_name,
                        tool_args=tool_args,
                        expected_result=step.expected_result
                    )
                )
            elif step.type == "semantic":
                if not step.question:
                    raise ValueError(_("Étape {idx} de type 'semantic' sans question").format(idx=idx))
                discovery_steps.append(
                    DiscoveryStep(
                        id=f"step_{idx}",
                        type=StepType.SEMANTIC,
                        description=step.description,
                        question=step.question,
                        expected_result=step.expected_result
                    )
                )
            else:
                raise ValueError(_("Type d'étape inconnu : {type}").format(type=step.type))

        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_END,
            data_type=self._data_type,
            targets=targets,
            technical_goals=technical_goals,
            step_count=len(discovery_steps),
            signature=signature,
        )

        return DiscoveryPlan(
            goal=goal,
            steps=discovery_steps,
            data_type=self._data_type,
            targets=targets,
            technical_goals=technical_goals,
            signature=signature
        )

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        return True

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        if not targets or not technical_goals:
            return f"{self._data_type}://unknown"
        if len(targets) == 1:
            return f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self._data_type}://multi/{targets_str}/{goals_str}"

"""
core/discovery/explorers/mission_history_explorer.py
====================================================
Explorer pour l'historique des missions d'une session.
Utilise le prompt générique explorer_plan_generation.md.
"""

import json
from typing import List, Dict, Any, Optional

from core.discovery.base_explorer import BaseExplorer
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType, ExplorerStep
from core.runtime_state import RuntimeState
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.constants import Events
from utils.logger import Logger
from pydantic import BaseModel, Field
from core.discovery.data_provider import DataProvider


class ExplorerPlanOutput(BaseModel):
    """
    Structure de réponse attendue du LLM de l'Explorer.
    Identique à celle utilisée par RegistryExplorer.
    """
    steps: List[ExplorerStep] = Field(
        ...,
        description=_("Liste des étapes à exécuter pour atteindre l'objectif.")
    )


class MissionHistoryExplorer(BaseExplorer):
    """
    Explorer pour interroger l'historique des missions.
    Utilise le même mécanisme de génération de plan que RegistryExplorer.
    """

    def __init__(self, runtime_state: RuntimeState, entity, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "missions"
        self.entity = entity
        self.llm = llm
        self._prompt_loader = get_prompt_loader()

    def get_data_type(self) -> str:
        return "missions"

    def get_available_goals(self) -> List[str]:
        return [
            "list_missions",
            "get_mission_summary",
            "get_mission_details",
            "search_missions_by_keyword"
        ]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_missions",
                "description": _(
                    "Retourne la liste des missions terminées dans la session courante "
                    "(ID, goal, status, date). Aucun paramètre requis."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "get_mission_summary",
                "description": _(
                    "Retourne le résumé stratégique d'une mission. "
                    "Paramètre requis : 'target' (mission_id ou index:0 pour la dernière)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id ou index (ex: index:0)"}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "get_mission_details",
                "description": _(
                    "Retourne les détails complets d'une mission (arbre d'exécution, registre résolu). "
                    "Paramètre requis : 'target' (mission_id ou index)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id ou index (ex: index:0)"}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "search_missions_by_keyword",
                "description": _(
                    "Recherche des missions dont le goal contient un mot‑clé. "
                    "Paramètre requis : 'keyword'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                    "required": ["keyword"]
                }
            }
        ]

    # =====================================================
    # EXÉCUTION DES OUTILS
    # =====================================================

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute un outil en utilisant le DataProvider "missions" de l'entité.
        """
        try:
            provider = self.entity.get_data_provider("missions")
            if not provider:
                return {"success": False, "data": None, "message": _("Aucun DataProvider 'missions' disponible.")}

            if tool_name == "list_missions":
                return await self._list_missions(provider)
            elif tool_name == "get_mission_summary":
                return await self._get_mission_summary(provider, args.get("target"))
            elif tool_name == "get_mission_details":
                return await self._get_mission_details(provider, args.get("target"))
            elif tool_name == "search_missions_by_keyword":
                return await self._search_missions(provider, args.get("keyword"))
            else:
                return {"success": False, "data": None, "message": _("Outil inconnu.")}
        except Exception as e:
            Logger.error(f"[MissionHistoryExplorer] Erreur : {e}")
            return {"success": False, "data": None, "message": str(e)}

    # =====================================================
    # GÉNÉRATION DE PLAN AVEC PROMPT GÉNÉRIQUE
    # =====================================================

    async def generate_plan(
        self,
        goal: str,
        technical_goal: str,
        target: str,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None
    ) -> DiscoveryPlan:
        """
        Génère un DiscoveryPlan en utilisant le prompt générique explorer_plan_generation.md.
        """
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("MissionHistoryExplorer n'a pas de LLM pour générer un plan."))

        available_goals = self.get_available_goals()
        if technical_goal not in available_goals:
            raise ValueError(
                _("Le goal technique '{technical_goal}' n'est pas supporté par MissionHistoryExplorer. "
                  "Goals disponibles : {goals}")
                .format(technical_goal=technical_goal, goals=", ".join(available_goals))
            )

        # Construction de la description des outils (format texte)
        tools_desc = self.get_tools_description()
        tools_text = "\n".join([
            f"- **{t['name']}** : {t['description']} (paramètres : {t.get('parameters', {})})"
            for t in tools_desc
        ])

        # Utilisation du prompt générique
        prompt = self._prompt_loader.load(
            "explorer_plan_generation.md",  # <-- Le même pour tous les explorateurs
            lang=getattr(self.runtime_state, "language", "en"),
            goal=goal,
            technical_goal=technical_goal,
            target=target,
            data_type=self._data_type,
            tools_description=tools_text
        )

        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_START,
            data_type=self._data_type,
            technical_goal=technical_goal,
            target=target,
            goal=goal,
            mission_id=self.runtime_state.current_mission_id
        )

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
                technical_goal=technical_goal,
                target=target,
                error=str(e),
                mission_id=self.runtime_state.current_mission_id
            )
            raise ValueError(
                _("Échec de la génération du plan par le LLM de l'Explorer : {error}")
                .format(error=e)
            )

        # Transformer les ExplorerStep en DiscoveryStep
        discovery_steps = []
        tool_names = [t["name"] for t in tools_desc]
        for idx, step in enumerate(steps):
            if step.type == "tool":
                if not step.tool_name:
                    raise ValueError(_("Étape {idx} de type 'tool' sans tool_name").format(idx=idx))
                if step.tool_name not in tool_names:
                    raise ValueError(
                        _("L'outil '{tool_name}' demandé n'existe pas pour MissionHistoryExplorer. "
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

        signature = self.create_signature(technical_goal, target)

        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_END,
            data_type=self._data_type,
            technical_goal=technical_goal,
            target=target,
            step_count=len(discovery_steps),
            mission_id=self.runtime_state.current_mission_id
        )

        return DiscoveryPlan(
            goal=goal,
            steps=discovery_steps,
            data_type=self._data_type,
            target=target,
            technical_goal=technical_goal,
            signature=signature
        )

    # =====================================================
    # VALIDATION ET SIGNATURE
    # =====================================================

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        if provider:
            return target in provider.get_targets()
        # Si on n'a pas de provider, on accepte toutes les cibles (sera validé plus tard)
        return True

    def create_signature(self, technical_goal: str, target: str) -> str:
        return f"{self._data_type}://{target}/{technical_goal}"

    # =====================================================
    # MÉTHODES PRIVÉES D'EXÉCUTION DES OUTILS
    # =====================================================

    async def _list_missions(self, provider) -> Dict[str, Any]:
        targets = provider.get_targets()
        missions = []
        for target in targets:
            meta = provider.get_metadata(target)
            if meta:
                missions.append({
                    "target": target,
                    "mission_id": meta.get("mission_id"),
                    "goal": meta.get("goal", ""),
                    "status": meta.get("status", ""),
                    "summary": meta.get("summary", ""),
                    "created_at": meta.get("created_at", ""),
                })
        return {"success": True, "data": {"missions": missions, "count": len(missions)}}

    async def _get_mission_summary(self, provider, target: str) -> Dict[str, Any]:
        if not target:
            return {"success": False, "data": None, "message": _("Le paramètre 'target' est requis.")}
        meta = provider.get_metadata(target)
        if not meta:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}
        return {"success": True, "data": {"summary": meta.get("summary", "Résumé non disponible")}}

    async def _get_mission_details(self, provider, target: str) -> Dict[str, Any]:
        if not target:
            return {"success": False, "data": None, "message": _("Le paramètre 'target' est requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}
        return {
            "success": True,
            "data": {
                "execution_tree": episode.get("execution_tree"),
                "resolved_data": episode.get("resolved_data"),
                "goal": episode.get("goal"),
                "status": episode.get("status"),
                "summary": episode.get("summary"),
            }
        }

    async def _search_missions(self, provider, keyword: str) -> Dict[str, Any]:
        if not keyword:
            return {"success": False, "data": None, "message": _("Le paramètre 'keyword' est requis.")}
        targets = provider.get_targets()
        results = []
        for target in targets:
            meta = provider.get_metadata(target)
            if meta and keyword.lower() in meta.get("goal", "").lower():
                results.append({
                    "target": target,
                    "mission_id": meta.get("mission_id"),
                    "goal": meta.get("goal", ""),
                    "status": meta.get("status", ""),
                    "summary": meta.get("summary", ""),
                })
        return {"success": True, "data": {"missions": results, "count": len(results)}}
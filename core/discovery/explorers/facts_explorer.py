"""
core/discovery/explorers/facts_explorer.py
======================================================
Explorer pour inspecter les faits mémorisés (profils, préférences, contraintes).
Implémente intégralement l'interface BaseExplorer (outils, exécution, génération de plan, signatures).
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


class FactsExplorer(BaseExplorer):
    """
    Explorer pour les faits sémantiques mémorisés.
    Permet à l'Orchestrateur d'explorer le profil utilisateur, les préférences et les faits globaux.
    """

    def __init__(self, runtime_state: RuntimeState, entity=None, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "facts"
        self.entity = entity
        self.llm = llm
        self._prompt_loader = get_prompt_loader()

    def get_data_type(self) -> str:
        return self._data_type

    def get_available_goals(self) -> List[str]:
        return [
            "list_facts",
            "get_user_profile",
            "get_preferences",
            "search_facts"
        ]

    def get_non_cacheable_goals(self) -> List[str]:
        """Les recherches libres par mot-clé ne doivent pas masquer d'autres requêtes."""
        return ["search_facts"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_facts",
                "description": _(
                    "Retourne la liste des faits mémorisés pour une cible donnée. "
                    "Paramètre requis : 'target' ('user_profile', 'preferences' ou 'all_facts')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["user_profile", "preferences", "all_facts"],
                            "description": "La catégorie de faits à inspecter"
                        }
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "get_user_profile",
                "description": _(
                    "Extrait spécifiquement les informations d'identité, nom, prénom, langue ou métier de l'utilisateur."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "get_preferences",
                "description": _(
                    "Extrait les préférences d'interaction, directives de style et souhaits de l'utilisateur."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "search_facts",
                "description": _(
                    "Recherche textuelle parmi tous les faits mémorisés contenant un mot-clé précis. "
                    "Paramètre requis : 'keyword'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "Le mot-clé à rechercher dans les faits"
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
                provider = self.entity.get_data_provider("facts")

            if tool_name == "list_facts":
                target = args.get("target", "all_facts")
                return await self._list_facts(provider, target)
            elif tool_name == "get_user_profile":
                return await self._list_facts(provider, "user_profile")
            elif tool_name == "get_preferences":
                return await self._list_facts(provider, "preferences")
            elif tool_name == "search_facts":
                keyword = args.get("keyword", "")
                return await self._search_facts(provider, keyword)
            else:
                return {"success": False, "data": None, "message": _("Outil inconnu : {}").format(tool_name)}
        except Exception as e:
            Logger.error(f"[FactsExplorer] Erreur lors de l'exécution de '{tool_name}' : {e}")
            return {"success": False, "data": None, "message": str(e)}

    async def _list_facts(self, provider, target: str) -> Dict[str, Any]:
        if not provider:
            return {"success": False, "data": None, "message": _("Aucun DataProvider 'facts' disponible.")}
        
        asset = provider.get_asset(target)
        if not asset:
            return {"success": False, "data": None, "message": f"Cible '{target}' non trouvée."}
        
        raw_data = getattr(asset, "data", {})
        facts = raw_data.get("facts", [])
        return {
            "success": True,
            "data": {
                "target": target,
                "count": len(facts),
                "facts": [f.get("fact") for f in facts if f.get("fact")]
            }
        }

    async def _search_facts(self, provider, keyword: str) -> Dict[str, Any]:
        if not provider:
            return {"success": False, "data": None, "message": _("Aucun DataProvider 'facts' disponible.")}
        
        if not keyword:
            return {"success": False, "data": None, "message": _("Le paramètre 'keyword' est requis.")}

        asset = provider.get_asset("all_facts")
        raw_data = getattr(asset, "data", {})
        facts = raw_data.get("facts", [])
        
        kw_lower = keyword.lower()
        matched = [f.get("fact") for f in facts if kw_lower in str(f.get("fact", "")).lower()]
        
        return {
            "success": True,
            "data": {
                "keyword": keyword,
                "count": len(matched),
                "results": matched
            }
        }

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        if provider:
            return target in provider.get_targets()
        return target in ["user_profile", "preferences", "all_facts"]

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        if not targets or not technical_goals:
            return f"{self._data_type}://unknown"
        if len(targets) == 1:
            return f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self._data_type}://multi/{targets_str}/{goals_str}"

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
            raise RuntimeError(_("FactsExplorer n'a pas de LLM pour générer un plan."))

        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets or not technical_goals:
            raise ValueError(_("Au moins une cible et un goal technique doivent être spécifiés."))
        if len(targets) != len(technical_goals):
            raise ValueError(_("Les listes 'targets' et 'technical_goals' doivent avoir la même longueur."))

        available_goals = self.get_available_goals()
        for tg in technical_goals:
            if tg not in available_goals:
                raise ValueError(
                    _("Le goal technique '{tg}' n'est pas supporté par FactsExplorer. Goals disponibles : {goals}")
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
                        _("L'outil '{tool_name}' demandé n'existe pas pour FactsExplorer. Outils disponibles : {tools}")
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

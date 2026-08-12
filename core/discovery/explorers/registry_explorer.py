"""
core/discovery/explorers/registry_explorer.py
=============================================
Explorer pour le registre des variables.
Génère un plan d'investigation en utilisant un LLM dédié.
Support multi‑cibles (listes de cibles et de goals techniques).
"""

import json
from typing import Dict, Any, List, Optional
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
    """
    steps: List[ExplorerStep] = Field(
        ...,
        description=_("Liste des étapes à exécuter pour atteindre l'objectif.")
    )


class RegistryExplorer(BaseExplorer):
    def __init__(self, runtime_state: RuntimeState, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "registry"
        self._prompt_loader = get_prompt_loader()
        self.llm = llm

    def get_data_type(self) -> str:
        return self._data_type

    def get_available_goals(self) -> List[str]:
        return ["list_keys", "describe_type", "check_value", "summarize"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_keys",
                "description": _("Retourne la liste des noms de variables disponibles. Aucun paramètre requis."),
                "parameters": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "describe_value",
                "description": _("Retourne les métadonnées d'une variable (type, description, source). Paramètre requis : 'target' (le nom de la variable)."),
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"]
                }
            },
            {
                "name": "inspect_value",
                "description": _("Retourne une portion de la valeur brute d'une variable (paginée). Paramètres requis : 'target' (le nom de la variable), optionnel : 'offset' et 'limit'."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 500}
                    },
                    "required": ["target"]
                }
            }
        ]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if tool_name == "list_keys":
                return await self._list_keys()
            elif tool_name == "describe_value":
                return await self._describe_value(args.get("target"))
            elif tool_name == "inspect_value":
                return await self._inspect_value(
                    args.get("target"),
                    args.get("offset", 0),
                    args.get("limit", 500)
                )
            else:
                return {"success": False, "data": _("Outil inconnu.")}
        except Exception as e:
            Logger.error(f"[RegistryExplorer] {_('Erreur {tool_name}')}: {e}".format(tool_name=tool_name))
            return {"success": False, "data": str(e)}

    async def generate_plan(
        self,
        goal: str,
        technical_goal: Optional[str] = None,
        target: Optional[str] = None,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None,
        # --- NOUVEAUX PARAMÈTRES MULTI‑CIBLES ---
        targets: Optional[List[str]] = None,
        technical_goals: Optional[List[str]] = None,
    ) -> DiscoveryPlan:
        """
        Génère un DiscoveryPlan pour explorer le registre.
        Supporte une ou plusieurs cibles via les listes `targets` et `technical_goals`.
        Les paramètres `target` et `technical_goal` sont conservés pour compatibilité,
        mais leur utilisation est dépréciée.
        """
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("RegistryExplorer n'a pas de LLM pour générer un plan."))

        # Normalisation : utiliser les listes si fournies, sinon les champs uniques
        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets or not technical_goals:
            raise ValueError(_("Au moins une cible et un goal technique doivent être spécifiés."))
        if len(targets) != len(technical_goals):
            raise ValueError(_("Les listes 'targets' et 'technical_goals' doivent avoir la même longueur."))

        # Vérifier que tous les goals sont disponibles
        available_goals = self.get_available_goals()
        for tg in technical_goals:
            if tg not in available_goals:
                raise ValueError(
                    _("Le goal technique '{tg}' n'est pas supporté par RegistryExplorer. Goals disponibles : {goals}")
                    .format(tg=tg, goals=", ".join(available_goals))
                )

        # Récupération du registre (priorité au data_context)
        registry = None
        if data_context is not None and isinstance(data_context, dict):
            registry = data_context
        elif llm and hasattr(llm, 'get_data_context'):
            ctx = llm.get_data_context()
            if isinstance(ctx, dict):
                registry = ctx
        if registry is None:
            registry = self._get_registry()

        # Validation des cibles (vérifier qu'elles existent dans le registre)
        for t in targets:
            if data_provider:
                if t not in data_provider.get_targets():
                    raise ValueError(_("La cible '{target}' n'existe pas dans les données fournies.").format(target=t))
            else:
                if t not in registry:
                    raise ValueError(_("Cible '{target}' invalide pour registry.").format(target=t))

        # Construction du prompt avec les listes
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
                targets=targets,
                technical_goals=technical_goals,
                error=str(e),
                mission_id=self.runtime_state.current_mission_id
            )
            raise ValueError(
                _("Échec de la génération du plan par le LLM de l'Explorer : {error}")
                .format(error=e)
            )

        # Transformer les étapes
        discovery_steps = []
        tool_names = [t["name"] for t in tools_desc]
        for idx, step in enumerate(steps):
            if step.type == "tool":
                if not step.tool_name:
                    raise ValueError(_("Étape {idx} de type 'tool' sans tool_name").format(idx=idx))
                if step.tool_name not in tool_names:
                    raise ValueError(
                        _("L'outil '{tool_name}' demandé n'existe pas pour le RegistryExplorer. Outils disponibles : {tools}")
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

        # Signature canonique pour le cache
        if len(targets) == 1:
            signature = f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        else:
            targets_str = "_".join(targets)
            goals_str = "_".join(technical_goals)
            signature = f"{self._data_type}://multi/{targets_str}/{goals_str}"

        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_END,
            data_type=self._data_type,
            targets=targets,
            technical_goals=technical_goals,
            step_count=len(discovery_steps),
            mission_id=self.runtime_state.current_mission_id
        )

        return DiscoveryPlan(
            goal=goal,
            steps=discovery_steps,
            data_type=self._data_type,
            targets=targets,
            technical_goals=technical_goals,
            signature=signature
        )

    # =====================================================
    # VALIDATION ET SIGNATURE (obsolètes)
    # =====================================================

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        """
        Validation d'une seule cible (conservée pour compatibilité).
        """
        if provider:
            return target in provider.get_targets()
        registry = self._get_registry()
        return target in registry

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        if not targets or not technical_goals:
            return f"{self._data_type}://unknown"
        if len(targets) == 1:
            return f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self._data_type}://multi/{targets_str}/{goals_str}"
    # =====================================================
    # MÉTHODES INTERNES
    # =====================================================

    def _get_registry(self) -> Dict[str, Any]:
        """Récupère le registre depuis le runtime_state (fallback)."""
        if hasattr(self.runtime_state, "variable_registry"):
            return self.runtime_state.variable_registry
        Logger.warning("[RegistryExplorer] Aucun registre trouvé.")
        return {}

    # =====================================================
    # MÉTHODES D'EXÉCUTION DES OUTILS (privées)
    # =====================================================

    async def _list_keys(self) -> Dict[str, Any]:
        registry = self._get_registry()
        keys = list(registry.keys())
        return {
            "success": True,
            "data": {
                "keys": keys,
                "count": len(keys)
            }
        }

    async def _describe_value(self, target: str) -> Dict[str, Any]:
        if not target:
            return {"success": False, "data": _("Aucune cible spécifiée.")}

        registry = self._get_registry()
        if target not in registry:
            return {"success": False, "data": _("La variable '{target}' n'existe pas dans le registre.").format(target=target)}

        info = registry[target]
        metadata = {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A"),
            "type": self._get_type_string(info.get("value"))
        }
        return {"success": True, "data": metadata}

    async def _inspect_value(self, target: str, offset: int = 0, limit: int = 500) -> Dict[str, Any]:
        if not target:
            return {"success": False, "data": _("Aucune cible spécifiée.")}

        registry = self._get_registry()
        if target not in registry:
            return {"success": False, "data": _("La variable '{target}' n'existe pas dans le registre.").format(target=target)}

        value = registry[target].get("value")
        if value is None:
            return {"success": True, "data": _("La variable '{target}' n'a pas de valeur.").format(target=target)}

        value_str = str(value)
        total_length = len(value_str)
        if offset >= total_length:
            return {
                "success": True,
                "data": "",
                "has_more": False,
                "next_offset": None,
                "total_length": total_length
            }

        end = min(offset + limit, total_length)
        chunk = value_str[offset:end]
        has_more = end < total_length
        next_offset = end if has_more else None

        return {
            "success": True,
            "data": chunk,
            "has_more": has_more,
            "next_offset": next_offset,
            "total_length": total_length
        }

    def _get_type_string(self, value) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        return "unknown"
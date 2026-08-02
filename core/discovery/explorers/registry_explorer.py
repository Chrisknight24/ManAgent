"""
core/discovery/explorers/registry_explorer.py
=============================================
Explorer pour le registre des variables.
Génère un plan d'investigation en utilisant un LLM dédié.
"""

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
        self.llm = llm  # LLM dédié pour la génération de plan

    # =====================================================
    # MÉTHODES D'INTERFACE
    # =====================================================

    def get_data_type(self) -> str:
        return self._data_type

    def get_available_goals(self) -> List[str]:
        return ["list_keys", "describe_type", "check_value", "summarize"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_keys",
                "description": _("Retourne la liste des noms de variables disponibles."),
                "parameters": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "describe_value",
                "description": _("Retourne les métadonnées d'une variable (type, description, source)."),
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"]
                }
            },
            {
                "name": "inspect_value",
                "description": _("Retourne une portion de la valeur brute d'une variable (paginée)."),
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

    # =====================================================
    # EXÉCUTION DES OUTILS
    # =====================================================

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Exécute un outil du registre."""
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

    # =====================================================
    # GÉNÉRATION DE PLAN (AVEC VRAI LLM)
    # =====================================================

    async def generate_plan(self, goal: str, technical_goal: str, target: str, llm: Optional[Llm] = None) -> DiscoveryPlan:
        """
        Génère un plan d'investigation en utilisant le LLM fourni ou celui de l'Explorer.
        """
        # Utiliser le LLM passé en priorité, sinon self.llm
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("RegistryExplorer n'a pas de LLM pour générer un plan."))

        # 1. Vérifier que le technical_goal est supporté
        available_goals = self.get_available_goals()
        if technical_goal not in available_goals:
            raise ValueError(
                _("Le goal technique '{technical_goal}' n'est pas supporté par RegistryExplorer. Goals disponibles : {goals}")
                .format(technical_goal=technical_goal, goals=", ".join(available_goals))
            )

        # 2. Préparer la description des outils
        tools_desc = self.get_tools_description()
        tools_text = "\n".join([
            f"- **{t['name']}** : {t['description']} (paramètres : {t.get('parameters', {})})"
            for t in tools_desc
        ])

        # 3. Charger le prompt
        prompt = self._prompt_loader.load(
            "explorer_plan_generation.md",
            lang=getattr(self.runtime_state, "language", "en"),
            goal=goal,
            technical_goal=technical_goal,
            target=target,
            data_type=self._data_type,
            tools_description=tools_text
        )

        # 4. Émettre un événement de début
        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_START,
            data_type=self._data_type,
            technical_goal=technical_goal,
            target=target,
            goal=goal
        )

        # 5. Appeler le LLM (effectif) pour obtenir les étapes
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
                error=str(e)
            )
            raise ValueError(
                _("Échec de la génération du plan par le LLM de l'Explorer : {error}")
                .format(error=e)
            )

        # 6. Valider et convertir les ExplorerStep en DiscoveryStep
        discovery_steps = []
        tool_names = [t["name"] for t in tools_desc]
        for idx, step in enumerate(steps):
            # ... (même validation)
            pass

        # 7. Construire la signature
        signature = self.create_signature(technical_goal, target)

        # 8. Émettre un événement de fin
        Logger.event(
            Events.DISCOVERY_PLAN_GENERATION_END,
            data_type=self._data_type,
            technical_goal=technical_goal,
            target=target,
            step_count=len(discovery_steps)
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
    # MÉTHODES DE VALIDATION ET SIGNATURE
    # =====================================================

    def validate_target(self, target: str) -> bool:
        """Vérifie que la cible existe dans le registre."""
        registry = self._get_registry()
        return target in registry

    def create_signature(self, technical_goal: str, target: str) -> str:
        """Crée une signature normalisée."""
        return f"{self._data_type}://{target}/{technical_goal}"

    def _get_registry(self) -> Dict[str, Any]:
        """Récupère le registre depuis le runtime_state."""
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
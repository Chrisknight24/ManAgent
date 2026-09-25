"""
core/discovery/explorers/world_explorer.py
==========================================
Explorer du monde vivant : lire l'état actuel via les outils de perception
de l'hôte, puis le faire comprendre (jamais de brut non lu).

Pattern : comme RegistryExplorer (plan tool + synthèse), sauf que la source
vient du MANIFESTE (kind=perception, vocabulaire ouvert) — aucun nom d'outil
en dur, robot compatible. Jamais enregistré dans le ToolsManager.
"""
from typing import Dict, Any, List, Optional
from core.discovery.base_explorer import BaseExplorer
from core.discovery.models import DiscoveryPlan
from core.runtime_state import RuntimeState
from core.llm import Llm
from utils.logger import Logger

GOALS = ["inspect_state", "locate_target", "verify_effect"]


class WorldExplorer(BaseExplorer):
    def __init__(self, runtime_state: RuntimeState, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "world"
        self.llm = llm

    def get_data_type(self) -> str:
        return self._data_type

    def get_scope_description(self) -> str:
        return (
            "Lit l'état actuel du monde via les outils de perception déclarés "
            "par l'hôte, puis le comprend (question ciblée ou rapport). "
            "À n'utiliser que si les données en main ne suffisent pas."
        )

    def get_available_goals(self) -> List[str]:
        return list(GOALS)

    def get_non_cacheable_goals(self) -> List[str]:
        # Le monde change : jamais de monde périmé servi du cache.
        return list(GOALS)

    def allow_successive_calls(self) -> bool:
        # Re-percevoir est légitime (vérifier après agir).
        return True

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "sense",
                "description": (
                    "Perçoit le monde via un outil de perception de l'hôte puis "
                    "le fait comprendre. Paramètres : 'question' (que chercher, "
                    "langage naturel), 'source_tool' (outil externe hôte, vide = "
                    "premier outil de perception déclaré), 'source_args' (dict), "
                    "'format_response' (format strict attendu, vide = rapport)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "source_tool": {"type": "string"},
                        "source_args": {"type": "object"},
                        "format_response": {"type": "string"},
                    },
                    "required": ["question"],
                },
            }
        ]

    def _perception_sources(self) -> List[str]:
        """Outils de perception déclarés au manifeste (kind=open, jamais en dur)."""
        try:
            tm = getattr(self.runtime_state, "tools_manager", None)
            if tm is not None and hasattr(tm, "perception_tool_names"):
                return sorted(tm.perception_tool_names())
        except Exception:
            pass
        return []

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name != "sense":
            return {"result": False, "data": None,
                    "error_reason": f"Outil inconnu : {tool_name}."}
        from tools.internal_tools import perceive_understand

        question = (args.get("question") or "").strip()
        if not question:
            return {"success": False, "data": "Paramètre 'question' requis."}
        source_tool = (args.get("source_tool") or "").strip()
        if not source_tool:
            sources = self._perception_sources()
            if not sources:
                return {
                    "result": False,
                    "data": None,
                    "error_reason": "Aucun outil de perception déclaré par l'hôte.",
                }
            source_tool = sources[0]
        return await perceive_understand(
            {
                "question": question,
                "source_tool": source_tool,
                "source_args": args.get("source_args") or {},
                "format_response": args.get("format_response") or "",
            },
            self.runtime_state,
        )

    async def generate_plan(
        self,
        goal: str,
        technical_goal: Optional[str] = None,
        target: Optional[str] = None,
        llm: Optional[Llm] = None,
        data_provider: Optional[Any] = None,
        data_context: Optional[Any] = None,
        targets: Optional[List[str]] = None,
        technical_goals: Optional[List[str]] = None,
    ) -> DiscoveryPlan:
        """Plan fixe (pas de LLM) : 1 perception ciblée + 1 synthèse."""
        from core.discovery.models import DiscoveryStep, StepType

        question = goal or "Décrire l'état actuel pertinent."
        steps = [
            DiscoveryStep(
                id="step_0",
                type=StepType.TOOL,
                description=f"Percevoir : {question}",
                tool_name="sense",
                tool_args={"question": question, "target": target or ""},
                expected_result="true",
            ),
            DiscoveryStep(
                id="step_1",
                type=StepType.SEMANTIC,
                description="Synthétiser l'essentiel pour la décision.",
                question=question,
                expected_result="true",
            ),
        ]
        sig_targets = targets or ([target] if target else ["live"])
        sig_goals = technical_goals or ([technical_goal] if technical_goal else ["inspect_state"])
        return DiscoveryPlan(
            goal=goal,
            steps=steps,
            data_type=self._data_type,
            targets=sig_targets,
            technical_goals=sig_goals,
            signature=f"{self._data_type}://live/{(technical_goals or ['inspect_state'])[0]}",
        )

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        return f"{self._data_type}://live/{(technical_goals or ['inspect_state'])[0]}"

    def validate_target(self, target: str, provider=None) -> bool:
        # Le monde n'a pas d'inventaire : toute cible libre est recevable.
        return True

from typing import List, Dict, Any, Optional
from core.entity import Entity
from utils.logger import Logger

class ToolsManager(Entity):
    def __init__(self, manager_id: str = "tools_manager", parent=None):
        super().__init__(name=manager_id, role="Tool Registry & Validator", parent=parent)
        self._registered_tools: Dict[str, Dict[str, Any]] = {}
        self._tool_sources: Dict[str, str] = {}  # source par nom d'outil

    def register_tool(
        self,
        name: str,
        role: str,
        description: str,
        parameters_schema: dict = None,
        source: str = "external"  # 'external' ou 'internal'
    ):
        """Enregistre un outil (externe ou interne)."""
        self._registered_tools[name] = {
            "name": name,
            "role": role,
            "description": description,
            "parameters": parameters_schema or {}
        }
        self._tool_sources[name] = source
        Logger.debug(f"[ToolsManager] Outil enregistré : {name} (source={source})")

    async def get_tools_view(
        self,
        goal_query: Optional[str] = None,
        source: Optional[str] = None  # 'external', 'internal', None = tout
    ) -> List[Dict[str, Any]]:
        """
        Retourne une vue formatée des outils.
        - source='external' : outils matériels (C++)
        - source='internal' : outils du Discovery Framework
        - source=None : tous les outils
        """
        view = []
        for tool in self._registered_tools.values():
            tool_name = tool["name"]
            if source is not None and self._tool_sources.get(tool_name) != source:
                continue
            view.append({
                "name": tool_name,
                "role": tool["role"],
                "description": tool["description"],
                "parameters": tool.get("parameters", {})
            })
        return view

    def validate_tool_call(self, tool_name: str, arguments: dict) -> bool:
        if tool_name not in self._registered_tools:
            Logger.warning(f"[ToolsManager] Tentative d'appel d'un outil inconnu : {tool_name}")
            return False
        return True

    async def process(self, *args, **kwargs) -> Any:
        pass

    def load_tools_from_payload(self, tools_list: List[Dict[str, Any]]):
        self._registered_tools.clear()
        self._tool_sources.clear()
        for t in tools_list:
            self.register_tool(
                name=t.get("name", "unknown_tool"),
                role=t.get("role", "Action_Matérielle"),
                description=t.get("description", ""),
                parameters_schema=t.get("parameters", {}),
                source="external"
            )
        Logger.info(f"[ToolsManager] Chargement terminé. {len(self._registered_tools)} outils reconnus.")
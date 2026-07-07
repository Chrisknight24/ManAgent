from typing import List, Dict, Any, Optional
from core.entity import Entity
from utils.logger import Logger

class ToolsManager(Entity):
    def __init__(self, manager_id: str = "tools_manager", parent=None):
        # Pas de LLM obligatoire pour l'instant, on reste sur du déterministe
        super().__init__(name=manager_id, role="Tool Registry & Validator", parent=parent)
        self._registered_tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, role: str, description: str, parameters_schema: dict = None):
        """Enregistre un outil provenant du C++."""
        self._registered_tools[name] = {
            "name": name,
            "role": role,
            "description": description,
            "parameters": parameters_schema or {}
        }
        Logger.debug(f"[ToolsManager] Outil enregistré : {name}")

    async def get_tools_view(self, goal_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retourne une vue formatée des outils. 
        Pour l'instant, ignore la 'goal_query' et retourne tout.
        """
        view = []
        for tool in self._registered_tools.values():
            view.append({
                "name": tool["name"],
                "role": tool["role"],
                "description": tool["description"],
                "parameters": tool.get("parameters", {}) # <-- AJOUTE CETTE LIGNE
            })
        return view
    
    def validate_tool_call(self, tool_name: str, arguments: dict) -> bool:
        """
        Vérifie si l'outil existe et si son appel semble légitime 
        avant de l'envoyer au bus d'événements.
        """
        if tool_name not in self._registered_tools:
            Logger.warning(f"[ToolsManager] Tentative d'appel d'un outil inconnu : {tool_name}")
            return False
        
        # Plus tard : Validation stricte des 'arguments' contre tool["parameters"] via jsonschema
        return True
        
    async def process(self, *args, **kwargs) -> Any:
        pass # Implémentation requise par le contrat Entity

    def load_tools_from_payload(self, tools_list: List[Dict[str, Any]]):
        """Prend la liste brute d'outils du payload et les enregistre proprement."""
        self._registered_tools.clear() # On s'assure de partir à neuf à chaque configuration
        for t in tools_list:
            # On utilise register_tool en extrayant les clés du dictionnaire JSON de Qt
            self.register_tool(
                name=t.get("name", "unknown_tool"),
                role=t.get("role", "Action_Matérielle"),
                description=t.get("description", ""),
                parameters_schema=t.get("parameters", {})
            )
        Logger.info(f"[ToolsManager] Chargement terminé. {len(self._registered_tools)} outils reconnus.")
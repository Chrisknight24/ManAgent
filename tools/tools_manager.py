"""
tools_manager.py
================
Gestionnaire des outils (externes et internes).
Hérite de Entity pour bénéficier de l'ID unique et des DataProviders.
Peut utiliser un LLM (passé en paramètre) pour interpréter des requêtes en langage naturel.
"""

import json
from typing import Dict, Any, List, Optional, Callable
from core.entity import Entity
try:
    from core.llm import Llm
except Exception:
    class Llm:
        pass
from utils.logger import Logger
from core.i18n import _
from core.prompt_loader import get_prompt_loader
try:
    from core.tools_models import ToolDecision
except Exception:
    class ToolDecision:
        pass
from core.constants import Events
from core.host_manifest import HostManifest


class ToolsManager(Entity):
    """
    Gestionnaire central des outils.
    - Outils externes : déclarés par le frontend (C++), utilisés par le Planner.
    - Outils internes : exécutés par le ToolsManager lui-même (extraction JSON, analyse, etc.).
    Peut utiliser un LLM (passé en paramètre) pour interpréter les requêtes en langage naturel.
    """

    def __init__(
        self,
        name: str = "tools_manager",
        llm: Optional[Llm] = None,
        parent: Optional[Entity] = None,
        runtime_state=None
    ):
        super().__init__(name=name, role="Tool Registry & Validator", llm=llm, parent=parent)
        self.runtime_state = runtime_state
        self._tools: Dict[str, Dict] = {}
        self._tool_handlers: Dict[str, Callable] = {}
        self._internal_tool_handlers: Dict[str, Callable] = {}
        self._internal_tools_metadata: Dict[str, Dict] = {}
        self._prompt_loader = get_prompt_loader()

        self._register_default_internal_tools()

    # =====================================================
    # OUTILS INTERNES (défauts)
    # =====================================================

    def _register_default_internal_tools(self):
        try:
            from tools.internal_tools import (
                extract_json_value,
                llm_analyze_data,
                llm_analyze_multi_data,
                execute_skill_tool,
            )

            self.register_internal_tool(
                name="extract_json_value",
                handler=extract_json_value,
                description=_(
                    "Extrait une valeur d'un objet JSON stocké dans une variable du registre. "
                    "Le paramètre 'data' doit être le NOM de la variable (ex: 'data_file_read' ou '$@_data_file_read'), "
                    "et non sa valeur brute. "
                    "Utilisez 'key' pour une extraction directe par clé, ou 'path' pour un chemin pointé."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "data": {"type": "string", "description": "Nom de la variable contenant le JSON"},
                        "key": {"type": "string", "description": "Clé à extraire"},
                        "path": {"type": "string", "description": "Chemin pointé (prioritaire sur key)"}
                    },
                    "required": ["data"]
                },
                capabilities=["extraire des valeurs depuis un JSON"]
            )

            self.register_internal_tool(
                name="llm_analyze_data",
                handler=llm_analyze_data,
                description=_(
                    "Analyse une donnée (JSON, CSV, texte, code) ou un fichier multimédia/visuel (image, photo, capture d'écran, schéma, document PDF, vidéo) à l'aide d'un LLM multimodal (vision & texte). "
                    "Requiert 'source' (nom de la variable ou de l'asset, ex: 'data_image_asset', 'files://photo.png') et 'query' (question ou instruction d'analyse en langage naturel). "
                    "Optionnels : 'from_line' et 'to_line' pour analyser une plage spécifique dans un fichier/texte volumineux. "
                    "Retourne le résultat détaillé de l'analyse dans 'data'. "
                    "N'utilisez cet outil QUE pour UNE SEULE variable : si la requête porte sur "
                    "DEUX variables ou plus (comparaison, cohérence, calcul croisé...), utilisez "
                    "'llm_analyze_multi_data' à la place."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Nom de la variable ou URI de l'asset contenant les données, l'image ou le document"},
                        "query": {"type": "string", "description": "Question ou consigne d'analyse en langage naturel"},
                        "from_line": {"type": "integer", "description": "Ligne de début optionnelle pour forage/découpage progressif"},
                        "to_line": {"type": "integer", "description": "Ligne de fin optionnelle pour forage/découpage progressif"}
                    },
                    "required": ["source", "query"]
                },
                capabilities=[
                    "analyser des données textuelles, structurées et du code",
                    "analyser le contenu d'une image, photo, capture d'écran ou schéma visuel avec un LLM multimodal (vision)",
                    "analyser des documents PDF et pièces jointes multimédias",
                    "effectuer de la reconnaissance visuelle, OCR et extraction sur des images"
                ]
            )

            self.register_internal_tool(
                name="llm_analyze_multi_data",
                handler=llm_analyze_multi_data,
                description=_(
                    "Analyse CONJOINTE de plusieurs variables à l'aide d'un LLM : comparaison, "
                    "cohérence, calcul croisé entre DEUX variables ou plus. "
                    "Requiert 'sources' (liste d'AU MOINS DEUX noms de variables) et 'query' "
                    "(question en langage naturel portant sur l'ensemble des sources). "
                    "N'utilisez PAS cet outil pour une seule variable : dans ce cas, utilisez "
                    "'llm_analyze_data' à la place."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": "Liste des noms de variables à analyser conjointement (au moins 2)"
                        },
                        "query": {"type": "string", "description": "Question en langage naturel portant sur l'ensemble des sources"}
                    },
                    "required": ["sources", "query"]
                },
                capabilities=[
                    "comparer plusieurs variables entre elles",
                    "analyser conjointement plusieurs sources de données",
                    "calculer une différence ou une cohérence entre deux variables ou plus"
                ]
            )

            self.register_internal_tool(
                name="execute_skill",
                handler=execute_skill_tool,
                description=_(
                    "Exécute un Skill composite ManAgent (méta-outil déterministe pré-qualifié). "
                    "Idéal et prioritaire pour accomplir des flux complexes récurrents à moindre coût et latence. "
                    "Requiert 'skill_id' (identifiant du Skill) et 'parameters' (dictionnaire des arguments)."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string", "description": "Identifiant unique du Skill (ex: 'desktop.excel.clean')"},
                        "parameters": {"type": "object", "description": "Paramètres d'entrée du Skill (supporte les pointeurs $@_data_xxx)"},
                        "version": {"type": "integer", "description": "Version optionnelle du Skill (défaut: version active en production)"}
                    },
                    "required": ["skill_id"]
                },
                capabilities=[
                    "exécuter un skill composite",
                    "lancer une automatisation de flux déterministe",
                    "exécuter une compétence qualifiée"
                ]
            )

            Logger.debug(
                "[ToolsManager] Outils internes enregistrés : "
                "extract_json_value, llm_analyze_data, llm_analyze_multi_data, execute_skill."
            )
        except ImportError as e:
            Logger.warning(f"[ToolsManager] Impossible d'importer les outils internes : {e}")

    def register_internal_tool(self, name: str, handler: Callable, description: str = "",
                               parameters_schema: dict = None, capabilities: List[str] = None) -> None:
        """
        Enregistre un outil interne avec ses métadonnées et ses capacités.
        Les capacités sont utilisées pour générer la description dynamique du tool_manager.
        """
        self._internal_tool_handlers[name] = handler
        self._internal_tools_metadata[name] = {
            "description": description,
            "parameters": parameters_schema or {},
            "capabilities": capabilities or []
        }
        Logger.debug(f"[ToolsManager] Outil interne '{name}' enregistré (caps: {capabilities}).")

    def _get_internal_tools_view(self) -> List[Dict]:
        """
        Retourne la vue unifiée des outils internes : tool_manager et load_literal_data,
        avec une description compilée dynamiquement à partir des sous-outils gérés et de leurs capacités.
        """
        tool_summaries = []
        all_caps = []
        for tool_name, meta in self._internal_tools_metadata.items():
            desc = meta.get("description", "")
            caps = meta.get("capabilities", [])
            for c in caps:
                if c not in all_caps:
                    all_caps.append(c)
            caps_str = f" [{', '.join(caps)}]" if caps else ""
            tool_summaries.append(f"  • {tool_name}{caps_str} : {desc}")

        compiled_tools_str = "\n".join(tool_summaries) if tool_summaries else "  • Aucun sous-outil"
        compiled_caps_str = ", ".join(all_caps) if all_caps else _("traitement et analyse de données")

        description = _(
            "Outil de délégation et chef d'orchestre d'analyse de données. "
            "Il permet d'exécuter des traitements et des analyses avancées via ses sous-outils internes gérés.\n"
            "Capacités dynamiques supportées : {caps}.\n"
            "Sous-outils actuellement gérés :\n{tools}\n"
            "Formulez simplement le besoin dans le paramètre 'request'."
        ).format(caps=compiled_caps_str, tools=compiled_tools_str)

        return [
            {
                "name": "tool_manager",
                "role": "Analyse de données (interne)",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "request": {"type": "string", "description": "Requête en langage naturel limitées aux capacités de l'outil"}
                    },
                    "required": ["request"]
                },
                "source": "internal"
            },
            {
                "name": "load_literal_data",
                "role": "Injection de données",
                "description": _("Permet d'injecter une valeur littérale brute (texte, JSON, liste, chiffre) dans le registre en la retournant telle quelle. Utilisez impérativement 'output_variable_name' dans l'étape du plan pour la sauvegarder."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "La valeur littérale brute à injecter (texte brut ou JSON sérialisé)"}
                    },
                    "required": ["value"]
                },
                "source": "internal"
            }
        ]

    def _get_internal_tools_description(self) -> str:
        """Utilisé pour le prompt interne du ToolsManager (pour lui-même)."""
        lines = []
        for name, meta in self._internal_tools_metadata.items():
            lines.append(f"- **{name}** : {meta['description']}")
            if meta.get('parameters'):
                lines.append(f"  Paramètres : {meta['parameters']}")
        return "\n".join(lines) if lines else "Aucun outil interne disponible."

    # =====================================================
    # OUTILS EXTERNES
    # =====================================================

    def register_host_manifest(self, manifest_data: Any) -> HostManifest:
        """
        Enregistre ou met à jour le manifeste dynamique de l'hôte (AutoCUse RPA ou autre).
        Permet à l'application hôte d'annoncer ses capacités et ses outils de manière
        totalement agnostique et dynamique.
        """
        if isinstance(manifest_data, HostManifest):
            host_manifest = manifest_data
        elif isinstance(manifest_data, dict):
            host_manifest = HostManifest.from_dict(manifest_data)
        else:
            host_manifest = HostManifest()

        # 1. Enregistrer dans runtime_state si disponible
        if self.runtime_state:
            self.runtime_state.host_manifest = host_manifest

        # 2. Charger les outils externes déclarés dans le manifest
        if host_manifest.tools:
            self.load_tools_from_payload(host_manifest.tools)

        Logger.info(
            f"[ToolsManager] 🌐 HostManifest enregistré : "
            f"host='{host_manifest.host_name}' v{host_manifest.host_version}, "
            f"os='{host_manifest.os}', "
            f"capacités={len(host_manifest.capabilities)}, "
            f"outils_externes={len(host_manifest.tools)}"
        )
        return host_manifest

    def get_host_manifest(self) -> Optional[HostManifest]:
        """Retourne le HostManifest courant."""
        if self.runtime_state and hasattr(self.runtime_state, "host_manifest"):
            return self.runtime_state.host_manifest
        return None

    def register_tool(self, name: str, role: str, description: str, parameters_schema: dict, source: str = "external") -> None:
        self._tools[name] = {
            "name": name,
            "role": role,
            "description": description,
            "parameters": parameters_schema,
            "source": source
        }
        Logger.debug(f"[ToolsManager] Outil enregistré : {name} (source={source})")

    def load_tools_from_payload(self, tools_payload: List[Dict]) -> None:
        for tool_def in tools_payload:
            name = tool_def.get("name")
            if not name:
                continue
            self.register_tool(
                name=name,
                role=tool_def.get("role", "Action_Matérielle"),
                description=tool_def.get("description", ""),
                parameters_schema=tool_def.get("parameters", {}),
                source=tool_def.get("source", "external")
            )

    async def get_tools_view(self, goal_query: str = None) -> List[Dict]:
        """Retourne la vue unifiée de TOUS les outils (externes + internes)."""
        external_tools = list(self._tools.values())
        internal_tools = self._get_internal_tools_view()
        return external_tools + internal_tools

    def validate_tool_call(self, tool_name: str, arguments: dict) -> bool:
        tool = self._tools.get(tool_name)
        if not tool:
            return False
        required = tool.get("parameters", {}).get("required", [])
        for param in required:
            if param not in arguments:
                return False
        return True

    # =====================================================
    # EXÉCUTION DES OUTILS (point d'entrée unique)
    # =====================================================

    async def execute_tool(self, tool_name: str, arguments: dict, llm: Optional[Llm] = None) -> str:
        """
        Point d'entrée unique pour tous les outils.
        """
        # 1. Outil spécial "tool_manager" / "analyze_data" : analyse par LLM
        if tool_name in ("tool_manager", "analyze_data"):
            request = arguments.get("request", "")
            if not request:
                return json.dumps({"result": False, "data": None, "message": "Requête vide."})
            result = await self.analyze_request(request, arguments, llm=llm)
            return json.dumps(result)
            
        # 1.5 Outil spécial "load_literal_data" : injection brute
        if tool_name == "load_literal_data":
            value = arguments.get("value", "")
            return json.dumps({"result": True, "data": value, "message": "Données chargées avec succès."})

        # 2. Outil interne (handler Python) – en principe, seul tool_manager les appelle, mais on laisse au cas où
        if tool_name in self._internal_tool_handlers:
            handler = self._internal_tool_handlers[tool_name]
            try:
                result = await handler(arguments, self.runtime_state)
                return json.dumps(result)
            except Exception as e:
                Logger.error(f"[ToolsManager] Erreur outil interne '{tool_name}' : {e}")
                return json.dumps({"result": False, "data": None, "message": str(e)})

        # 3. Outil externe (C++ / frontend) → délégation à l'Orchestrateur
        if hasattr(self.runtime_state, 'orchestrator'):
            result_str = await self.runtime_state.orchestrator._execute_external_tool(tool_name, arguments)
            return result_str
        else:
            return json.dumps({"result": False, "data": None, "message": f"Outil externe '{tool_name}' non géré."})

    async def _execute_external_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Méthode appelée par l'Orchestrateur pour exécuter un outil externe.
        (Cette méthode est en fait dans l'Orchestrateur, mais on la garde ici pour référence)
        """
        # Cette méthode est déléguée à l'Orchestrateur. On ne l'implémente pas ici.
        # La logique externe est dans Orchestrator._execute_external_tool.
        pass

    # =====================================================
    # ANALYSE PAR LLM (requête en langage naturel)
    # =====================================================

    async def analyze_request(self, request: str, context: dict, llm: Optional[Llm] = None) -> Dict[str, Any]:
        effective_llm = llm or self.llm
        if not effective_llm:
            return {"result": False, "data": None, "message": _("Aucun LLM disponible.")}

        # Récupération des champs de contexte pour le rattachement
        exec_ctx = getattr(self.runtime_state, 'execution_context', {})
        solver_id = exec_ctx.get("solver_id")
        attempt_number = exec_ctx.get("attempt_number")
        step_id = exec_ctx.get("step_id")
        # mission_id lu depuis le contexte scopé, pas depuis l'ancien attribut
        # global `current_mission_id` (jamais remis à None après une mission,
        # cf. le correctif fait sur le Discovery Framework — même cause,
        # même remède : voir execution_context.py / logger.py).
        mission_id = exec_ctx.get("mission_id")
        span_id = exec_ctx.get("span_id")

        # Construction de la vue métadonnées du registre (uniquement depuis le temporaire)
        registry_metadata_str = _("Aucune variable disponible dans le registre.")
        temp_registry = getattr(self.runtime_state, "_solver_registry_for_tools", None)
        if temp_registry:
            var_registry = temp_registry
            Logger.debug(f"[ToolsManager] Utilisation du registre temporaire avec {len(var_registry)} variables.")
            if var_registry:
                lines = []
                for name, info in var_registry.items():
                    var_type = info.get("type", "unknown")
                    desc = info.get("description", _("Pas de description"))
                    lines.append(f"- `$@_{name}` : {desc} (type: {var_type})")
                registry_metadata_str = "\n".join(lines)
        else:
            Logger.warning("[ToolsManager] Aucun registre temporaire trouvé.")

        internal_tools_description = self._get_internal_tools_description()

        prompt = self._prompt_loader.load(
            "tools_manager_analysis.md",
            lang=getattr(self.runtime_state, "language", "fr"),
            request=request,
            context=context,
            internal_tools_description=internal_tools_description,
            registry_metadata=registry_metadata_str
        )

        try:
            decision: ToolDecision = await effective_llm.generate_structured(
                prompt=prompt,
                schema=ToolDecision,
                tag="tools_manager_decision"
            )

            # Parser tool_args_json
            tool_args = {}
            if decision.success:
                try:
                    if decision.tool_args_json and decision.tool_args_json.strip():
                        tool_args = json.loads(decision.tool_args_json)
                    else:
                        # tool_args_json vide alors que success=True => échec
                        return {
                            "result": False,
                            "data": None,
                            "error_reason": _("La décision a indiqué un succès mais tool_args_json est vide.")
                        }
                except json.JSONDecodeError as e:
                    Logger.error(f"[ToolsManager] JSON invalide dans tool_args_json : {decision.tool_args_json} ({e})")
                    return {
                        "result": False,
                        "data": None,
                        "error_reason": _("tool_args_json n'est pas un JSON valide.")
                    }

            # Émission de l'événement de décision
            Logger.event(
                Events.TOOLS_MANAGER_DECISION,
                request=request,
                decision_success=decision.success,
                tool_name=decision.tool_name if decision.success else None,
                tool_args=tool_args if decision.success else {},
                rejection_reason=decision.rejection_reason if not decision.success else None,
                mission_id=mission_id,
                span_id=span_id,
                solver_id=solver_id,
                attempt_number=attempt_number,
                step_id=step_id
            )

            if not decision.success:
                return {
                    "result": False,
                    "data": None,
                    "error_reason": decision.rejection_reason or _("Aucun outil interne ne correspond à la requête.")
                }

            if decision.tool_name not in self._internal_tool_handlers:
                return {
                    "result": False,
                    "data": None,
                    "error_reason": _("L'outil '{tool_name}' n'existe pas.").format(tool_name=decision.tool_name)
                }

            handler = self._internal_tool_handlers[decision.tool_name]

            Logger.event(
                Events.TOOLS_MANAGER_EXECUTION,
                tool_name=decision.tool_name,
                tool_args=tool_args,
                mission_id=mission_id,
                span_id=span_id,
                solver_id=solver_id,
                attempt_number=attempt_number,
                step_id=step_id
            )

            # --- PASSER LE LLM AU HANDLER ---
            self.runtime_state._tools_llm = effective_llm
            try:
                result = await handler(tool_args, self.runtime_state)
            finally:
                self.runtime_state._tools_llm = None
            # --------------------------------

            Logger.event(
                Events.TOOLS_MANAGER_RESULT,
                tool_name=decision.tool_name,
                result=result.get("result"),
                data=result.get("data"),
                message=result.get("message"),
                mission_id=mission_id,
                span_id=span_id,
                solver_id=solver_id,
                attempt_number=attempt_number,
                step_id=step_id
            )
            result["error_reason"] = result.get('error_reason', '') or result.get('message', '')
            return result

        except Exception as e:
            Logger.error(f"[ToolsManager] Erreur lors de l'analyse LLM : {e}")
            return {
                "result": False,
                "data": None,
                "error_reason": _("Échec de l'analyse : {error}").format(error=str(e))
            }

    # =====================================================
    # MÉTHODES ABSTRAITES DE Entity
    # =====================================================

    async def process(self, *args, **kwargs) -> Any:
        request = kwargs.get("request", args[0] if args else "")
        if not request:
            return {"result": False, "message": "Aucune requête fournie."}
        llm = kwargs.get("llm")
        return await self.analyze_request(request, kwargs.get("context", {}), llm=llm)

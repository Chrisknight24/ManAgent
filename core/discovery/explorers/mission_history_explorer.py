"""
core/discovery/explorers/mission_history_explorer.py
====================================================
Explorer pour l'historique des missions d'une session.
Utilise le prompt générique explorer_plan_generation.md.
Support multi‑cibles et condensation des données volumineuses.
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
        # Longueur maximale pour les données textuelles avant troncature
        self.max_data_length = getattr(runtime_state, "max_data_length", 1000)

    def get_data_type(self) -> str:
        return "missions"

    def get_available_goals(self) -> List[str]:
        return [
            "list_missions",
            "get_mission_summary",
            "get_mission_details",
            "search_missions_by_keyword",
            "analyze_registry",
            "analyze_execution_tree",
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
            },
            {
                "name": "analyze_registry",
                "description": _(
                    "Analyse le registre de variables résolues d'une mission pour répondre à une question précise. "
                    "Paramètres requis : 'target' (mission_id) et 'question' (la question en langage naturel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "question": {"type": "string", "description": "Question en langage naturel sur le registre"}
                    },
                    "required": ["target", "question"]
                }
            },
            {
                "name": "analyze_execution_tree",
                "description": _(
                    "Analyse l'arbre d'exécution d'une mission pour répondre à une question précise. "
                    "Paramètres requis : 'target' (mission_id) et 'question' (la question en langage naturel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "question": {"type": "string", "description": "Question en langage naturel sur l'arbre d'exécution"}
                    },
                    "required": ["target", "question"]
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
            elif tool_name == "analyze_registry":
                return await self._analyze_registry(provider, args.get("target"), args.get("question"))
            elif tool_name == "analyze_execution_tree":
                return await self._analyze_execution_tree(provider, args.get("target"), args.get("question"))
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
        technical_goal: Optional[str] = None,
        target: Optional[str] = None,
        llm: Optional[Llm] = None,
        data_provider: Optional['DataProvider'] = None,
        data_context: Optional[Any] = None,
        targets: Optional[List[str]] = None,
        technical_goals: Optional[List[str]] = None,
    ) -> DiscoveryPlan:
        """
        Génère un DiscoveryPlan en utilisant le prompt générique explorer_plan_generation.md.
        Supporte le multi‑cibles via les listes `targets` et `technical_goals`.
        """
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("MissionHistoryExplorer n'a pas de LLM pour générer un plan."))

        # Normalisation
        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets or not technical_goals:
            raise ValueError(_("Au moins une cible et un goal technique doivent être spécifiés."))
        if len(targets) != len(technical_goals):
            raise ValueError(_("Les listes 'targets' et 'technical_goals' doivent avoir la même longueur."))

        # Vérifier que tous les goals sont valides
        available_goals = self.get_available_goals()
        for tg in technical_goals:
            if tg not in available_goals:
                raise ValueError(
                    _("Le goal technique '{tg}' n'est pas supporté par MissionHistoryExplorer. Goals disponibles : {goals}")
                    .format(tg=tg, goals=", ".join(available_goals))
                )

        # Construction de la description des outils
        tools_desc = self.get_tools_description()
        tools_text = "\n".join([
            f"- **{t['name']}** : {t['description']} (paramètres : {t.get('parameters', {})})"
            for t in tools_desc
        ])

        # Utilisation du prompt générique
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
    # VALIDATION ET SIGNATURE
    # =====================================================

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        """
        Valide que la cible est accessible.
        Dans le cas des missions, on accepte toute cible (la validation réelle est faite à l'exécution).
        """
        return True

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        """
        Crée une signature normalisée pour le cache à partir des listes de cibles et de goals.
        """
        if not targets or not technical_goals:
            return f"{self._data_type}://unknown"
        if len(targets) == 1:
            return f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self._data_type}://multi/{targets_str}/{goals_str}"

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

    # =====================================================
    # ANALYSE DU REGISTRE AVEC CONDENSATION
    # =====================================================

    async def _analyze_registry(self, provider, target: str, question: str) -> Dict[str, Any]:
        if not target or not question:
            return {"success": False, "data": None, "message": _("Les paramètres 'target' et 'question' sont requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        registry = episode.get("resolved_data")
        if registry is None:
            return {"success": False, "data": None, "message": _("Aucun registre résolu disponible pour cette mission.")}
        if not registry:
            return {"success": True, "data": _("Le registre de cette mission est vide (aucune variable cruciale enregistrée).")}

        # Condensation des données volumineuses
        condensed_registry = {}
        for k, v in registry.items():
            condensed_registry[k] = self._condense_value(v, self.max_data_length)

        registry_str = json.dumps(condensed_registry, indent=2, ensure_ascii=False)

        llm_to_use = self.llm or self.runtime_state.discovery_llm
        if not llm_to_use:
            return {"success": False, "data": None, "message": _("Aucun LLM disponible pour l'analyse.")}

        prompt = self._prompt_loader.load(
            "analyze_registry.md",
            lang=getattr(self.runtime_state, "language", "en"),
            registry_data=registry_str,
            question=question
        )

        try:
            response = await llm_to_use.generate_text(prompt, tag="analyze_registry")
            return {"success": True, "data": response}
        except Exception as e:
            Logger.error(f"[MissionHistoryExplorer] Erreur lors de l'analyse du registre : {e}")
            return {"success": False, "data": None, "message": f"Erreur lors de l'analyse : {str(e)}"}

    # =====================================================
    # ANALYSE DE L'ARBRE D'EXÉCUTION AVEC CONDENSATION
    # =====================================================

    async def _analyze_execution_tree(self, provider, target: str, question: str) -> Dict[str, Any]:
        if not target or not question:
            return {"success": False, "data": None, "message": _("Les paramètres 'target' et 'question' sont requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        tree = episode.get("execution_tree")
        if tree is None:
            return {"success": False, "data": None, "message": _("Aucun arbre d'exécution disponible pour cette mission.")}
        if not tree:
            return {"success": True, "data": _("L'arbre d'exécution de cette mission est vide.")}

        # Condensation de l'arbre (limiter la taille du JSON)
        tree_str = json.dumps(tree, indent=2, ensure_ascii=False)
        if len(tree_str) > self.max_data_length * 5:  # on autorise un peu plus pour l'arbre
            tree_str = tree_str[:self.max_data_length * 5] + "\n... (contenu tronqué)"

        llm_to_use = self.llm or self.runtime_state.discovery_llm
        if not llm_to_use:
            return {"success": False, "data": None, "message": _("Aucun LLM disponible pour l'analyse.")}

        prompt = self._prompt_loader.load(
            "analyze_execution_tree.md",
            lang=getattr(self.runtime_state, "language", "en"),
            execution_tree_data=tree_str,
            question=question
        )

        try:
            response = await llm_to_use.generate_text(prompt, tag="analyze_execution_tree")
            return {"success": True, "data": response}
        except Exception as e:
            Logger.error(f"[MissionHistoryExplorer] Erreur lors de l'analyse de l'arbre : {e}")
            return {"success": False, "data": None, "message": f"Erreur lors de l'analyse : {str(e)}"}

    # =====================================================
    # UTILITAIRE : CONDENSATION DES DONNÉES
    # =====================================================

    def _condense_value(self, value: Any, max_length: int = 1000) -> Any:
        """
        Condense une valeur pour l'affichage dans un prompt.
        - Si c'est une chaîne longue, on la tronque.
        - Si c'est une liste/dict, on la tronque en préservant la structure.
        - Les types simples (bool, int, float) sont conservés tels quels.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if len(value) > max_length:
                return f"{value[:max_length]}... (longueur: {len(value)} caractères)"
            return value
        if isinstance(value, list):
            # On condense chaque élément de la liste
            condensed = [self._condense_value(item, max_length) for item in value]
            # Si la représentation JSON est trop longue, on tronque
            if len(json.dumps(condensed, ensure_ascii=False)) > max_length:
                return condensed[:5]  # on limite à 5 éléments
            return condensed
        if isinstance(value, dict):
            condensed = {}
            for k, v in value.items():
                condensed[k] = self._condense_value(v, max_length)
            if len(json.dumps(condensed, ensure_ascii=False)) > max_length * 2:
                # On réduit le nombre de clés
                keys = list(condensed.keys())[:10]
                return {k: condensed[k] for k in keys}
            return condensed
        # Autres types (bytes, etc.) : on les convertit en chaîne
        return str(value)
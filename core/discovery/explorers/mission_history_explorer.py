"""
core/discovery/explorers/mission_history_explorer.py
====================================================
Explorer pour l'historique des missions d'une session.
Architecture Tool-Based avec Drill-Down hiérarchique et pagination.
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
    """
    steps: List[ExplorerStep] = Field(
        ...,
        description=_("Liste des étapes à exécuter pour atteindre l'objectif.")
    )


class MissionHistoryExplorer(BaseExplorer):
    """
    Explorer pour interroger l'historique des missions.
    Supporte la navigation progressive : Outline -> Filtres (Échecs) -> Détails d'étapes -> Pagination.
    """

    def __init__(self, runtime_state: RuntimeState, entity, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "missions"
        self.entity = entity
        self.llm = llm
        self._prompt_loader = get_prompt_loader()
        self.max_data_length = getattr(runtime_state, "max_data_length", 2000)

    def get_data_type(self) -> str:
        return "missions"

    def get_scope_description(self) -> str:
        return "Examine l'arbre d'exécution, le statut des étapes et l'historique des missions enregistrées."

    def get_available_goals(self) -> List[str]:
        return [
            "list_missions",
            "get_mission_summary",
            "get_mission_details",
            "get_execution_tree_outline",
            "get_failed_steps",
            "get_step_details",
            "get_sub_mission_tree",
            "inspect_step_output",
            "search_missions_by_keyword",
            "analyze_registry",
            "analyze_execution_tree",
        ]

    def get_non_cacheable_goals(self) -> List[str]:
        """
        Goals sémantiques dépendant de la question libre (ne doivent pas être mis en cache par signature seule).
        """
        return ["analyze_registry", "analyze_execution_tree"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_missions",
                "description": _(
                    "Retourne la liste des missions enregistrées dans la session (ID, goal, status, timestamps). "
                    "Aucun paramètre requis."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "get_mission_summary",
                "description": _(
                    "Retourne le résumé textuel court d'une mission. "
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
                "name": "get_execution_tree_outline",
                "description": _(
                    "Retourne la structure compacte de l'arbre d'exécution (tentatives, IDs d'étapes, "
                    "outils appelés, statuts, durée) sans les gros payloads de données. Idéal pour avoir la vue d'ensemble."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "get_failed_steps",
                "description": _(
                    "Retourne UNIQUEMENT les étapes ayant échoué ou divergé lors de l'exécution, avec leurs "
                    "arguments d'outils complets, le résultat réel et la raison exacte de l'erreur."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "get_step_details",
                "description": _(
                    "Retourne les détails intégraux d'une étape précise (tool_args, attendu vs obtenu, statut, logs). "
                    "Paramètres requis : 'target' (mission_id) et 'step_id' (l'identifiant de l'étape, ex: 'step_01')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "step_id": {"type": "string", "description": "Identifiant de l'étape (ex: 'step_01')"}
                    },
                    "required": ["target", "step_id"]
                }
            },
            {
                "name": "get_sub_mission_tree",
                "description": _(
                    "Retourne l'arbre d'exécution d'un sous-solver / sous-tâche enfant (abstract_task). "
                    "Paramètres requis : 'target' (mission_id parent) et 'step_id' (l'étape de délégation) ou 'solver_id'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id parent"},
                        "step_id": {"type": "string", "description": "step_id de la sous-tâche"}
                    },
                    "required": ["target", "step_id"]
                }
            },
            {
                "name": "inspect_step_output",
                "description": _(
                    "Permet d'inspecter et paginer le retour brut volumineux d'une étape précise. "
                    "Paramètres : 'target', 'step_id', et optionnels 'offset' (défaut 0), 'limit' (défaut 1000)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "step_id": {"type": "string", "description": "step_id de l'étape"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 1000}
                    },
                    "required": ["target", "step_id"]
                }
            },
            {
                "name": "get_mission_details",
                "description": _(
                    "Retourne les détails complets condensés d'une mission (arbre résumé et registre). "
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
                    "Recherche des missions dont le goal contient un mot-clé. Paramètre requis : 'keyword'."
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
                    "Analyse sémantique du registre des variables résolues d'une mission. "
                    "Paramètres requis : 'target' (mission_id) et 'question' (langage naturel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "question": {"type": "string", "description": "Question sur le registre"}
                    },
                    "required": ["target", "question"]
                }
            },
            {
                "name": "analyze_execution_tree",
                "description": _(
                    "Analyse sémantique globale de l'arbre d'exécution d'une mission. "
                    "Paramètres requis : 'target' (mission_id) et 'question' (langage naturel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "mission_id"},
                        "question": {"type": "string", "description": "Question sur l'arbre d'exécution"}
                    },
                    "required": ["target", "question"]
                }
            }
        ]

    # =====================================================
    # EXÉCUTION DES OUTILS
    # =====================================================

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            provider = self.entity.get_data_provider("missions")
            if not provider:
                return {"success": False, "data": None, "message": _("Aucun DataProvider 'missions' disponible.")}

            target = args.get("target")

            if tool_name == "list_missions":
                return await self._list_missions(provider)
            elif tool_name == "get_mission_summary":
                return await self._get_mission_summary(provider, target)
            elif tool_name == "get_execution_tree_outline":
                return await self._get_execution_tree_outline(provider, target)
            elif tool_name == "get_failed_steps":
                return await self._get_failed_steps(provider, target)
            elif tool_name == "get_step_details":
                return await self._get_step_details(provider, target, args.get("step_id"))
            elif tool_name == "get_sub_mission_tree":
                return await self._get_sub_mission_tree(provider, target, args.get("step_id"))
            elif tool_name == "inspect_step_output":
                return await self._inspect_step_output(
                    provider, target, args.get("step_id"),
                    offset=int(args.get("offset", 0)),
                    limit=int(args.get("limit", 1000))
                )
            elif tool_name == "get_mission_details":
                return await self._get_mission_details(provider, target)
            elif tool_name == "search_missions_by_keyword":
                return await self._search_missions(provider, args.get("keyword"))
            elif tool_name == "analyze_registry":
                return await self._analyze_registry(provider, target, args.get("question"))
            elif tool_name == "analyze_execution_tree":
                return await self._analyze_execution_tree(provider, target, args.get("question"))
            else:
                return {"success": False, "data": None, "message": _("Outil inconnu : {}").format(tool_name)}
        except Exception as e:
            Logger.error(f"[MissionHistoryExplorer] Erreur lors de l'exécution de '{tool_name}' : {e}")
            return {"success": False, "data": None, "message": str(e)}

    # =====================================================
    # MÉTHODES SPÉCIALISÉES (DRILL-DOWN & OUTILS)
    # =====================================================

    async def _list_missions(self, provider) -> Dict[str, Any]:
        targets = provider.get_targets()
        missions = []
        for t in targets:
            meta = provider.get_metadata(t)
            if meta:
                missions.append({
                    "target": t,
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

    async def _get_execution_tree_outline(self, provider, target: str) -> Dict[str, Any]:
        """Squelette léger de l'arbre pour une vue d'ensemble sans surcharge de tokens."""
        if not target:
            return {"success": False, "data": None, "message": _("Le paramètre 'target' est requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        tree = episode.get("execution_tree")
        if not tree:
            return {"success": True, "data": _("Aucun arbre d'exécution disponible pour cette mission.")}

        def build_outline(node_tree):
            outline = {
                "solver_id": node_tree.get("solver_id"),
                "goal": node_tree.get("goal"),
                "status": node_tree.get("status"),
                "attempts_count": len(node_tree.get("attempts", [])),
                "attempts": []
            }
            for att in node_tree.get("attempts", []):
                att_info = {
                    "attempt_number": att.get("attempt_number"),
                    "outcome": att.get("outcome"),
                    "failure_class": att.get("failure_class"),
                    "steps": []
                }
                for n in att.get("nodes", []):
                    step_brief = {
                        "step_id": n.get("step_id"),
                        "description": n.get("description"),
                        "type": n.get("step_type"),
                        "status": n.get("status"),
                        "tool_name": n.get("tool_name"),
                        "has_child_tree": bool(n.get("child_execution_tree"))
                    }
                    att_info["steps"].append(step_brief)
                outline["attempts"].append(att_info)
            return outline

        return {"success": True, "data": build_outline(tree)}

    async def _get_failed_steps(self, provider, target: str) -> Dict[str, Any]:
        """Extrait exhaustivement toutes les étapes en échec avec leurs tool_args intégraux."""
        if not target:
            return {"success": False, "data": None, "message": _("Le paramètre 'target' est requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        tree = episode.get("execution_tree")
        if not tree:
            return {"success": True, "data": {"failed_steps": [], "message": "Aucun arbre d'exécution."}}

        failed_nodes = []

        def collect_failures(node_tree, depth_label=""):
            for att in node_tree.get("attempts", []):
                for n in att.get("nodes", []):
                    status = str(n.get("status", "")).lower()
                    if status in ["failed", "skipped", "error"] or n.get("error_reason"):
                        failed_nodes.append({
                            "scope": depth_label or node_tree.get("solver_id"),
                            "attempt_number": att.get("attempt_number"),
                            "step_id": n.get("step_id"),
                            "description": n.get("description"),
                            "step_type": n.get("step_type"),
                            "tool_name": n.get("tool_name"),
                            "tool_args": n.get("tool_args"),
                            "expected_result": n.get("expected_result"),
                            "actual_result": n.get("actual_result"),
                            "error_reason": n.get("error_reason"),
                            "status": n.get("status")
                        })
                    if n.get("child_execution_tree"):
                        collect_failures(n.get("child_execution_tree"), f"Sous-tâche [{n.get('step_id')}]")

        collect_failures(tree)
        return {
            "success": True,
            "data": {
                "failed_count": len(failed_nodes),
                "failed_steps": failed_nodes
            }
        }

    async def _get_step_details(self, provider, target: str, step_id: str) -> Dict[str, Any]:
        """Trouve une étape spécifique par son step_id dans l'arbre entier et renvoie sa vue 100% complète."""
        if not target or not step_id:
            return {"success": False, "data": None, "message": _("Les paramètres 'target' et 'step_id' sont requis.")}

        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        tree = episode.get("execution_tree")
        if not tree:
            return {"success": False, "data": None, "message": "Aucun arbre d'exécution disponible."}

        target_step_clean = step_id.strip().lower()

        def find_step(node_tree):
            for att in node_tree.get("attempts", []):
                for n in att.get("nodes", []):
                    if str(n.get("step_id", "")).strip().lower() == target_step_clean:
                        return {
                            "solver_id": node_tree.get("solver_id"),
                            "attempt_number": att.get("attempt_number"),
                            "node": n
                        }
                    if n.get("child_execution_tree"):
                        found = find_step(n.get("child_execution_tree"))
                        if found:
                            return found
            return None

        result = find_step(tree)
        if not result:
            return {"success": False, "data": None, "message": f"Étape '{step_id}' introuvable dans la mission '{target}'."}

        node = result["node"]
        return {
            "success": True,
            "data": {
                "mission_id": target,
                "solver_id": result["solver_id"],
                "attempt_number": result["attempt_number"],
                "step_id": node.get("step_id"),
                "description": node.get("description"),
                "step_type": node.get("step_type"),
                "tool_name": node.get("tool_name"),
                "tool_args": node.get("tool_args"),
                "expected_result": node.get("expected_result"),
                "actual_result": node.get("actual_result"),
                "error_reason": node.get("error_reason"),
                "status": node.get("status"),
                "started_at": node.get("started_at"),
                "ended_at": node.get("ended_at"),
                "has_child_execution_tree": bool(node.get("child_execution_tree"))
            }
        }

    async def _get_sub_mission_tree(self, provider, target: str, step_id: str) -> Dict[str, Any]:
        """Retourne l'arbre d'exécution d'un sous-solver enfant."""
        step_res = await self._get_step_details(provider, target, step_id)
        if not step_res.get("success"):
            return step_res

        # Récupérer directement l'arbre enfant
        episode = provider.get_data(target)
        tree = episode.get("execution_tree")
        target_step_clean = step_id.strip().lower()

        def find_child_tree(node_tree):
            for att in node_tree.get("attempts", []):
                for n in att.get("nodes", []):
                    if str(n.get("step_id", "")).strip().lower() == target_step_clean:
                        return n.get("child_execution_tree")
                    if n.get("child_execution_tree"):
                        found = find_child_tree(n.get("child_execution_tree"))
                        if found:
                            return found
            return None

        child_tree = find_child_tree(tree)
        if not child_tree:
            return {"success": False, "data": None, "message": f"L'étape '{step_id}' n'a pas généré de sous-arbre d'exécution."}

        return {
            "success": True,
            "data": {
                "parent_step_id": step_id,
                "sub_tree": self._summarize_execution_tree(child_tree)
            }
        }

    async def _inspect_step_output(self, provider, target: str, step_id: str, offset: int = 0, limit: int = 1000) -> Dict[str, Any]:
        """Pagine les résultats bruts volumineux d'une étape."""
        step_res = await self._get_step_details(provider, target, step_id)
        if not step_res.get("success"):
            return step_res

        actual_result = step_res["data"].get("actual_result")
        if actual_result is None:
            return {"success": True, "data": "", "total_length": 0, "has_more": False}

        raw_str = actual_result if isinstance(actual_result, str) else json.dumps(actual_result, ensure_ascii=False)
        total_len = len(raw_str)

        if offset >= total_len:
            return {"success": True, "data": "", "offset": offset, "total_length": total_len, "has_more": False}

        chunk = raw_str[offset:offset + limit]
        has_more = (offset + limit) < total_len

        return {
            "success": True,
            "data": chunk,
            "offset": offset,
            "chunk_length": len(chunk),
            "total_length": total_len,
            "has_more": has_more,
            "next_offset": (offset + limit) if has_more else None
        }

    async def _get_mission_details(self, provider, target: str) -> Dict[str, Any]:
        if not target:
            return {"success": False, "data": None, "message": _("Le paramètre 'target' est requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        raw_tree = episode.get("execution_tree")
        raw_resolved = episode.get("resolved_data") or {}

        return {
            "success": True,
            "data": {
                "execution_tree": self._summarize_execution_tree(raw_tree) if raw_tree else None,
                "resolved_data": {
                    k: self._condense_value(v, self.max_data_length) for k, v in raw_resolved.items()
                },
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
        for t in targets:
            meta = provider.get_metadata(t)
            if meta and keyword.lower() in meta.get("goal", "").lower():
                results.append({
                    "target": t,
                    "mission_id": meta.get("mission_id"),
                    "goal": meta.get("goal", ""),
                    "status": meta.get("status", ""),
                    "summary": meta.get("summary", ""),
                })
        return {"success": True, "data": {"missions": results, "count": len(results)}}

    # =====================================================
    # ANALYSES SÉMANTIQUES (ROBUSTES ET SANS TRONCATURE DUPLICATIVE)
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
            return {"success": True, "data": _("Le registre de cette mission est vide.")}

        condensed_registry = {k: self._condense_value(v, self.max_data_length) for k, v in registry.items()}
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

    async def _analyze_execution_tree(self, provider, target: str, question: str) -> Dict[str, Any]:
        if not target or not question:
            return {"success": False, "data": None, "message": _("Les paramètres 'target' et 'question' sont requis.")}
        episode = provider.get_data(target)
        if not episode:
            return {"success": False, "data": None, "message": f"Mission '{target}' non trouvée."}

        tree = episode.get("execution_tree")
        if not tree:
            return {"success": True, "data": _("L'arbre d'exécution de cette mission est vide.")}

        tree_summary = self._summarize_execution_tree(tree)
        tree_str = json.dumps(tree_summary, indent=2, ensure_ascii=False)

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
    # RÉSUMÉ STRUCTURÉ & CONDENSATION PROPRE
    # =====================================================

    def _summarize_execution_tree(self, tree: Dict[str, Any], depth: int = 0, max_depth: int = 4) -> Dict[str, Any]:
        """
        Génère un résumé de haute fidélité conservant les tool_args condensés et les causes racines.
        """
        if not tree:
            return {}

        summary = {
            "solver_id": tree.get("solver_id"),
            "goal": tree.get("goal"),
            "status": tree.get("status"),
            "started_at": tree.get("started_at"),
            "ended_at": tree.get("ended_at"),
        }

        attempts = tree.get("attempts") or []
        summarized_attempts = []
        for att in attempts:
            steps_summary = []
            for node in (att.get("nodes") or []):
                step_entry = {
                    "step_id": node.get("step_id"),
                    "description": node.get("description"),
                    "type": node.get("step_type"),
                    "status": node.get("status"),
                }
                if node.get("tool_name"):
                    step_entry["tool_name"] = node.get("tool_name")
                if node.get("tool_args"):
                    # CONSERVATION DES TOOL_ARGS (condensés proprement au lieu d'être jetés)
                    step_entry["tool_args"] = self._condense_value(node.get("tool_args"), max_length=500)
                if node.get("expected_result") is not None:
                    step_entry["expected_result"] = node.get("expected_result")
                if node.get("actual_result") is not None:
                    step_entry["actual_result"] = self._condense_value(node.get("actual_result"), max_length=500)
                if node.get("error_reason"):
                    step_entry["error_reason"] = node.get("error_reason")

                child_tree = node.get("child_execution_tree")
                if child_tree:
                    if depth < max_depth:
                        step_entry["sub_mission"] = self._summarize_execution_tree(
                            child_tree, depth=depth + 1, max_depth=max_depth
                        )
                    else:
                        step_entry["sub_mission"] = _(
                            "(sous-mission disponible via 'get_sub_mission_tree')"
                        )
                steps_summary.append(step_entry)

            attempt_entry = {
                "attempt_number": att.get("attempt_number"),
                "outcome": att.get("outcome"),
                "steps": steps_summary,
            }
            if att.get("failure_class") and att.get("failure_class") != "none":
                attempt_entry["failure_class"] = att.get("failure_class")
            if att.get("failure_reason"):
                attempt_entry["failure_reason"] = att.get("failure_reason")
            summarized_attempts.append(attempt_entry)

        summary["attempts_count"] = len(summarized_attempts)
        summary["attempts"] = summarized_attempts
        return summary

    def _condense_value(self, value: Any, max_length: int = 1000) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if len(value) > max_length:
                return f"{value[:max_length]}... [taille totale: {len(value)} car.]"
            return value
        if isinstance(value, list):
            condensed = [self._condense_value(item, max_length) for item in value]
            if len(json.dumps(condensed, ensure_ascii=False)) > max_length:
                return condensed[:8]
            return condensed
        if isinstance(value, dict):
            condensed = {k: self._condense_value(v, max_length) for k, v in value.items()}
            if len(json.dumps(condensed, ensure_ascii=False)) > max_length * 2:
                keys = list(condensed.keys())[:15]
                return {k: condensed[k] for k in keys}
            return condensed
        return str(value)

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
        effective_llm = llm or self.llm
        if not effective_llm:
            raise RuntimeError(_("MissionHistoryExplorer n'a pas de LLM pour générer un plan."))

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
                    _("Le goal technique '{tg}' n'est pas supporté par MissionHistoryExplorer. Goals disponibles : {goals}")
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

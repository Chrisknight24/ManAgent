from typing import List, Dict, Any, Optional
import json
import re
from core.discovery.base_explorer import BaseExplorer
from core.discovery.data_asset import DataAsset
from core.discovery.asset_registry import AssetRegistry
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType, ExplorerStep
from core.runtime_state import RuntimeState
from core.llm import Llm
from core.discovery.data_provider import DataProvider
from core.i18n import _
from utils.logger import Logger


class FilesExplorer(BaseExplorer):
    """
    Explorer universel pour forer dans les DataAssets textuels, fichiers et gros messages utilisateurs.
    Permet la recherche, la lecture par tranche et la structure sans charger tout le fichier d'un coup.
    Conforme à l'interface BaseExplorer du Discovery Framework.
    """
    MAX_SLICE_CHARS = 4000  # Protection anti-débordement de contexte

    def __init__(self, runtime_state: RuntimeState, registry: Optional[AssetRegistry] = None, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self.registry = registry
        self.llm = llm

    def get_data_type(self) -> str:
        return "files"

    def get_scope_description(self) -> str:
        return "Explore et analyse les fichiers physiques (sources, outputs, assets) du workspace."

    def allow_successive_calls(self) -> bool:
        """FilesExplorer autorise la lecture par tranches et la recherche progressive."""
        return True

    def get_available_goals(self) -> List[str]:
        return [
            "read_asset_head",
            "read_asset_tail",
            "read_asset_slice",
            "search_asset",
            "get_asset_summary",
            "inspect_asset"
        ]

    def get_non_cacheable_goals(self) -> List[str]:
        return ["search_asset"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "read_asset_head",
                "description": _("Lit les premières lignes d'un fichier ou d'un DataAsset."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": _("Nom ou URI de l'asset")},
                        "n_lines": {"type": "integer", "default": 20, "description": _("Nombre de lignes à lire (max: 100)")}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "read_asset_tail",
                "description": _("Lit les dernières lignes d'un fichier ou d'un DataAsset."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": _("Nom ou URI de l'asset")},
                        "n_lines": {"type": "integer", "default": 20, "description": _("Nombre de lignes à lire (max: 100)")}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "read_asset_slice",
                "description": _("Lit une tranche spécifique de lignes d'un asset."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": _("Nom ou URI de l'asset")},
                        "from_line": {"type": "integer", "default": 1, "description": _("Numéro de ligne début")},
                        "to_line": {"type": "integer", "default": 50, "description": _("Numéro de ligne fin")}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "search_asset",
                "description": _("Recherche des occurrences de texte ou regex dans un asset volumineux."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": _("Nom ou URI de l'asset")},
                        "query": {"type": "string", "description": _("Mot-clé court ou expression exacte à rechercher (ex: 'ToolsManager' ou 'error'). Ne METTEZ PAS de phrase de consigne en français.")},
                        "regex": {"type": "boolean", "default": False, "description": _("Recherche par regex")},
                        "limit": {"type": "integer", "default": 20, "description": _("Nombre max de correspondances")}
                    },
                    "required": ["target", "query"]
                }
            },
            {
                "name": "inspect_asset",
                "description": _("Retourne les métadonnées techniques complètes (taille, lignes, hash, encodage) et un aperçu."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": _("Nom ou URI de l'asset")}
                    },
                    "required": ["target"]
                }
            }
        ]

    def _resolve_asset(self, target_name: str) -> Optional[DataAsset]:
        if self.registry:
            asset = self.registry.resolve_asset(target_name)
            if asset:
                return asset
        # Essayer via le runtime_state ou discovery_engine
        if hasattr(self.runtime_state, "discovery_engine") and self.runtime_state.discovery_engine:
            provider = self.runtime_state.discovery_engine.get_explorer("files")
            if provider and hasattr(provider, "registry") and provider.registry:
                return provider.registry.resolve_asset(target_name)
        return None

    def _extract_search_pattern(self, query: str) -> str:
        """Extrait un mot-clé précis, un horodatage ou un motif au lieu d'une phrase complète en langage naturel."""
        if not query:
            return ""
        query = query.strip()
        time_match = re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', query)
        if time_match:
            return time_match.group(0)
        quoted = re.findall(r'["\']([^"\']+)["\']', query)
        if quoted:
            return quoted[0]
        words = [w for w in query.split() if w]
        if len(words) <= 2:
            return query
        stopwords = {"dans", "avec", "pour", "cette", "fichier", "input", "turn", "les", "des", "lignes", "logs", "horaire", "autour", "afficher", "re", "et"}
        tech_words = [w for w in words if re.search(r'[A-Z0-9_.:]', w) and w.lower() not in stopwords]
        if tech_words:
            return tech_words[0]
        clean_words = [w for w in words if w.lower() not in stopwords]
        return " ".join(clean_words[:2]) if clean_words else query

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Exécute un outil de forage déterministe sur un DataAsset."""
        target_name = args.get("target", "")
        asset = self._resolve_asset(target_name)

        if not asset:
            return {"success": False, "data": _("Asset introuvable pour la cible '{}'.").format(target_name)}

        try:
            if tool_name == "read_asset_head":
                n = int(args.get("n_lines", 20))
                lines = asset.get_head(n_lines=min(n, 100))
                text = "\n".join(lines)
                if len(text) > self.MAX_SLICE_CHARS:
                    text = text[:self.MAX_SLICE_CHARS] + "\n... [tronqué pour budget de tokens]"
                return {
                    "success": True,
                    "data": {
                        "uri": asset.get_uri(),
                        "lines": lines,
                        "count": len(lines),
                        "verbatim": f"Premières {len(lines)} lignes de `{asset.get_uri()}` :\n```\n{text}\n```"
                    }
                }

            elif tool_name == "read_asset_tail":
                n = int(args.get("n_lines", 20))
                lines = asset.get_tail(n_lines=min(n, 100))
                text = "\n".join(lines)
                if len(text) > self.MAX_SLICE_CHARS:
                    text = text[:self.MAX_SLICE_CHARS] + "\n... [tronqué pour budget de tokens]"
                return {
                    "success": True,
                    "data": {
                        "uri": asset.get_uri(),
                        "lines": lines,
                        "count": len(lines),
                        "verbatim": f"Dernières {len(lines)} lignes de `{asset.get_uri()}` :\n```\n{text}\n```"
                    }
                }

            elif tool_name == "read_asset_slice":
                from_line = max(1, int(args.get("from_line", 1)))
                to_line = max(from_line, int(args.get("to_line", from_line + 49)))
                lines = asset.read_slice(from_line=from_line, to_line=to_line)
                text = "\n".join(lines)
                if len(text) > self.MAX_SLICE_CHARS:
                    text = text[:self.MAX_SLICE_CHARS] + "\n... [tronqué pour budget de tokens]"
                return {
                    "success": True,
                    "data": {
                        "uri": asset.get_uri(),
                        "lines": lines,
                        "from_line": from_line,
                        "to_line": to_line,
                        "verbatim": f"Lignes {from_line} à {to_line} de `{asset.get_uri()}` :\n```\n{text}\n```"
                    }
                }

            elif tool_name == "search_asset":
                raw_query = str(args.get("query", ""))
                is_regex = bool(args.get("regex", False))
                limit = int(args.get("limit", 20))

                search_query = self._extract_search_pattern(raw_query) or raw_query
                results = asset.search_lines(query=search_query, regex=is_regex, limit=limit)

                if not results:
                    return {
                        "success": True,
                        "data": {
                            "uri": asset.get_uri(),
                            "matches": [],
                            "query": search_query,
                            "verbatim": f"Aucune occurrence trouvée pour '{search_query}' dans `{asset.get_uri()}`."
                        }
                    }

                lines_fmt = [f"Ligne {r['line_number']}: {r['content']}" for r in results]
                text = "\n".join(lines_fmt)
                if len(text) > self.MAX_SLICE_CHARS:
                    text = text[:self.MAX_SLICE_CHARS] + "\n... [résultats supplémentaires tronqués]"
                return {
                    "success": True,
                    "data": {
                        "uri": asset.get_uri(),
                        "matches": results,
                        "count": len(results),
                        "verbatim": f"{len(results)} occurrences trouvées pour '{search_query}' dans `{asset.get_uri()}` :\n```\n{text}\n```"
                    }
                }

            elif tool_name == "inspect_asset":
                meta = asset.asset_meta
                preview = asset.get_preview(max_lines=10, max_chars=1000)
                info = (
                    f"**URI**: `{asset.get_uri()}`\n"
                    f"- Nom: {meta.name if meta else asset.target_id}\n"
                    f"- Taille: {round((meta.size_bytes if meta else 0)/1024, 2)} Ko ({meta.line_count if meta else '?'} lignes, ~{meta.token_estimate if meta else '?'} tokens)\n"
                    f"- Tranche recommandée pour lecture progressive: 50 à 100 lignes (`read_asset_slice`)\n"
                    f"- SHA-256: `{meta.sha256_hash if meta else 'N/A'}`\n"
                    f"- Capacités: {', '.join(asset.get_capabilities())}\n"
                    f"**Aperçu** :\n```\n{preview}\n```"
                )
                return {
                    "success": True,
                    "data": {
                        "uri": asset.get_uri(),
                        "metadata": meta.model_dump() if meta else {},
                        "verbatim": info
                    }
                }

            return {"success": False, "data": f"Outil inconnu '{tool_name}' pour FilesExplorer."}

        except Exception as e:
            Logger.error(f"[FilesExplorer] Erreur lors de l'exécution de {tool_name}: {e}")
            return {"success": False, "data": f"Erreur lors de l'exécution de '{tool_name}': {str(e)}"}

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        return True

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        if not targets or not technical_goals:
            return f"{self.get_data_type()}://unknown"
        if len(targets) == 1:
            return f"{self.get_data_type()}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self.get_data_type()}://multi/{targets_str}/{goals_str}"

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
        """Génère un plan d'investigation déterministe pour l'asset ciblé."""
        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets:
            targets = ["default"]
        if not technical_goals:
            technical_goals = ["inspect_asset"]

        steps: List[DiscoveryStep] = []
        for idx, (t, tg) in enumerate(zip(targets, technical_goals)):
            tool_name = tg
            if tg not in self.get_available_goals():
                tool_name = "inspect_asset"

            args: Dict[str, Any] = {"target": t}
            if "search" in tool_name or "find" in tool_name:
                tool_name = "search_asset"
                clean_q = self._extract_search_pattern(goal)
                args["query"] = clean_q or "error"
            elif "slice" in tool_name:
                tool_name = "read_asset_slice"
                args["from_line"] = 1
                args["to_line"] = 50
            elif "tail" in tool_name:
                tool_name = "read_asset_tail"
                args["n_lines"] = 25
            elif "head" in tool_name:
                tool_name = "read_asset_head"
                args["n_lines"] = 25

            steps.append(DiscoveryStep(
                id=f"step_{idx+1}",
                type=StepType.TOOL,
                tool_name=tool_name,
                tool_args=args,
                description=f"Action '{tool_name}' sur l'asset '{t}'"
            ))

        signature = self.create_signature(targets, technical_goals)

        return DiscoveryPlan(
            goal=goal,
            steps=steps,
            data_type=self.get_data_type(),
            targets=targets,
            technical_goals=technical_goals,
            signature=signature
        )


"""
tools/internal_tools.py
=======================
Outils internes pour l'analyse de données structurées.
"""

import json
import re
from typing import Dict, Any, Optional
from core.prompt_loader import get_prompt_loader
from core.tools_models import AnalysisResult
from utils.logger import Logger
from core.i18n import _


async def resolve_variable(var_name: str, runtime_state) -> Any:
    """
    Résout une variable nommée (ex: '$@_data_file_content', 'data_file_content' ou 'inputs://turn_1')
    en consultant le registre du solver courant dans runtime_state et l'AssetRegistry.
    Retourne la valeur brute ou le contenu texte complet de l'asset.
    """
    if not var_name:
        return None

    if isinstance(var_name, str) and var_name.startswith("$@_"):
        var_name = var_name[3:]

    temp_registry = getattr(runtime_state, "_solver_registry_for_tools", None)
    raw_val = None
    source_uri = None

    if temp_registry is not None and isinstance(temp_registry, dict):
        # 1. Correspondance directe par nom de variable
        if var_name in temp_registry:
            info = temp_registry[var_name]
            if isinstance(info, dict):
                if info.get("type") == "asset" and "asset" in info:
                    return info["asset"].dump_data()
                source_uri = info.get("source_uri") or info.get("value")
                raw_val = info.get("value")
            else:
                raw_val = info

        # 2. Si non trouvé par le nom, chercher si la clé correspond à l'URI source dans le registre
        if raw_val is None:
            for k, info in temp_registry.items():
                if isinstance(info, dict):
                    if info.get("source_uri") == var_name or info.get("value") == var_name:
                        if info.get("type") == "asset" and "asset" in info:
                            return info["asset"].dump_data()
                        source_uri = info.get("source_uri") or info.get("value")
                        raw_val = info.get("value")
                        break

    # 3. Résolution d'asset physique si la valeur est une URI d'asset ou un asset virtuel
    candidate_uri = None
    for item in [raw_val, source_uri, var_name]:
        if isinstance(item, str) and any(item.startswith(p) for p in ["inputs://", "outputs://", "files://"]):
            candidate_uri = item
            break

    # Résolution de l'asset_registry de manière robuste
    asset_registry = getattr(runtime_state, "current_asset_registry", None)
    if not asset_registry:
        asset_registry = getattr(runtime_state, "asset_registry", None)
    if not asset_registry and hasattr(runtime_state, "discovery_engine") and runtime_state.discovery_engine:
        for dtype in ["files", "inputs", "outputs"]:
            explorer = runtime_state.discovery_engine.get_explorer(dtype)
            if explorer and hasattr(explorer, "registry") and explorer.registry:
                asset_registry = explorer.registry
                break

    if candidate_uri and asset_registry:
        asset = asset_registry.resolve_asset(candidate_uri)
        if asset:
            return asset.dump_data()

    if raw_val is not None:
        return raw_val

    # 4. Repli ultime : chercher directement l'asset par son nom/URI dans l'AssetRegistry
    if asset_registry:
        asset = asset_registry.resolve_asset(var_name)
        if asset:
            return asset.dump_data()

    return None


async def extract_json_value(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """Extrait une valeur d'un objet JSON à partir d'une clé ou d'un chemin."""
    data_var = args.get("data")
    key = args.get("key")
    path = args.get("path")

    if not data_var:
        return {"result": False, "data": None, "error_reason": "Le paramètre 'data' est requis."}

    raw_value = await resolve_variable(data_var, runtime_state)
    if raw_value is None:
        return {"result": False, "data": None, "error_reason": f"Variable '{data_var}' introuvable."}

    parsed = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {"result": False, "data": None, "error_reason": "La variable ne contient pas un JSON valide."}

    if path:
        try:
            tokens = re.split(r'\.|\[|\]', path)
            tokens = [t for t in tokens if t]
            current = parsed
            for tok in tokens:
                if tok.isdigit():
                    current = current[int(tok)]
                else:
                    current = current.get(tok)
                if current is None:
                    break
            if current is not None:
                return {"result": True, "data": current, "message": "Extraction réussie."}
            else:
                return {"result": False, "data": None, "error_reason": f"Chemin '{path}' non trouvé."}
        except Exception as e:
            return {"result": False, "data": None, "error_reason": f"Erreur d'extraction : {str(e)}"}

    elif key:
        if isinstance(parsed, dict):
            if key in parsed:
                return {"result": True, "data": parsed[key], "message": "Extraction réussie."}
            else:
                return {"result": False, "data": None, "error_reason": f"Clé '{key}' non trouvée."}
        else:
            return {"result": False, "data": None, "error_reason": "La variable n'est pas un objet JSON."}

    return {"result": False, "data": None, "error_reason": "Impossible d'extraire : fournissez 'key' ou 'path'."}


async def _get_tools_llm(runtime_state) -> Optional[Any]:
    """Résout le LLM à utiliser pour une analyse interne (posé temporairement
    par ToolsManager.analyze_request avant l'appel au handler)."""
    llm = getattr(runtime_state, "_tools_llm", None)
    if not llm:
        llm = getattr(runtime_state.tools_manager, "llm", None)
    return llm


async def _run_llm_analysis(data: Any, query: str, runtime_state, tag: str) -> Dict[str, Any]:
    """
    Logique commune d'appel LLM pour l'analyse de données, factorisée entre
    `llm_analyze_data` (une source) et `llm_analyze_multi_data` (plusieurs
    sources) — les deux ne diffèrent que par la RÉSOLUTION des variables en
    amont, pas par l'appel LLM lui-même. `data` peut être une valeur unique
    ou un dict {nom_variable: valeur} pour le cas multi-source ; dans les
    deux cas c'est le même template `llm_analyze_data.md` qui est utilisé.
    """
    llm = await _get_tools_llm(runtime_state)
    if not llm:
        return {
            "result": False,
            "data": None,
            "error_reason": _("Aucun LLM disponible pour l'analyse.")
        }

    # Protection anti-débordement de contexte pour les données massives
    MAX_PROMPT_DATA_CHARS = 12000
    safe_data = data
    if isinstance(data, str) and len(data) > MAX_PROMPT_DATA_CHARS:
        safe_data = data[:MAX_PROMPT_DATA_CHARS] + f"\n\n... [Données tronquées pour analyse LLM : {len(data)} caractères au total. Utilisez une extraction ciblée ou le DiscoveryEngine pour forer.]"
    elif isinstance(data, dict):
        # Vérifier si l'un des champs est volumineux
        dict_str = json.dumps(data, ensure_ascii=False)
        if len(dict_str) > MAX_PROMPT_DATA_CHARS:
            safe_data = {}
            for k, v in data.items():
                v_str = str(v)
                if len(v_str) > (MAX_PROMPT_DATA_CHARS // max(1, len(data))):
                    safe_data[k] = v_str[:(MAX_PROMPT_DATA_CHARS // max(1, len(data)))] + " ... [tronqué]"
                else:
                    safe_data[k] = v

    loader = get_prompt_loader()
    prompt = loader.load(
        "llm_analyze_data.md",
        lang=getattr(runtime_state, "language", "en"),
        data=safe_data,
        query=query
    )

    try:
        analysis: AnalysisResult = await llm.generate_structured(
            prompt=prompt,
            schema=AnalysisResult,
            tag=tag
        )
        msg = getattr(analysis, "message", None) or getattr(analysis, "error_reason", None) or _("Analyse terminée.")
        return {
            "result": analysis.success,
            "data": analysis.data,
            "error_reason": msg,
            "message": msg
        }
    except Exception as e:
        Logger.error(f"[{tag}] Erreur : {e}")
        err_msg = _("Erreur lors de l'analyse : {error}").format(error=str(e))
        return {
            "result": False,
            "data": None,
            "error_reason": err_msg,
            "message": err_msg
        }


async def llm_analyze_data(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Analyse une donnée (variable) à l'aide d'un LLM.
    
    Args:
        args (dict): Doit contenir "source" (nom de la variable) et "query" (question).
        runtime_state: L'état runtime (contient le LLM, le registre, etc.)
    
    Retourne:
        dict: {"result": bool, "data": Any, "error_reason": str}
    """
    source = args.get("source")
    query = args.get("query")

    if not source or not query:
        msg = _("Les paramètres 'source' et 'query' sont requis.")
        return {
            "result": False,
            "data": None,
            "error_reason": msg,
            "message": msg
        }

    raw_value = await resolve_variable(source, runtime_state)
    if raw_value is None:
        msg = _("Variable '{source}' introuvable.").format(source=source)
        return {
            "result": False,
            "data": None,
            "error_reason": msg,
            "message": msg
        }

    return await _run_llm_analysis(raw_value, query, runtime_state, tag="llm_analyze_data")


async def llm_analyze_multi_data(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Analyse CONJOINTE de plusieurs variables à l'aide d'un LLM (comparaison,
    cohérence, calcul croisé entre deux ou plusieurs sources, etc.).

    Contrairement à `llm_analyze_data` (une seule source), cet outil accepte
    une LISTE de noms de variables. Chacune est résolue via le même registre
    temporaire, puis combinée en un seul objet structuré {nom: valeur} envoyé
    au même template de prompt que `llm_analyze_data` — pas besoin d'un
    template dédié, le LLM voit clairement quelle valeur porte quel nom.

    Args:
        args (dict) : doit contenir "sources" (liste d'AU MOINS DEUX noms de
            variables) et "query" (question portant sur l'ensemble).
        runtime_state : idem llm_analyze_data.

    Retourne:
        dict: {"result": bool, "data": Any, "error_reason": str}
    """
    sources = args.get("sources")
    query = args.get("query")

    if not query:
        msg = _("Le paramètre 'query' est requis.")
        return {
            "result": False,
            "data": None,
            "error_reason": msg,
            "message": msg
        }

    if not sources or not isinstance(sources, list) or len(sources) < 2:
        msg = _(
            "Le paramètre 'sources' doit être une liste d'AU MOINS DEUX noms de "
            "variables. Pour une seule variable, utilisez plutôt 'llm_analyze_data'."
        )
        return {
            "result": False,
            "data": None,
            "error_reason": msg,
            "message": msg
        }

    resolved: Dict[str, Any] = {}
    missing: list = []
    for name in sources:
        clean_name = name[3:] if isinstance(name, str) and name.startswith("$@_") else name
        value = await resolve_variable(clean_name, runtime_state)
        if value is None:
            missing.append(clean_name)
        else:
            resolved[clean_name] = value

    if missing:
        msg = _("Variable(s) introuvable(s) : {missing}").format(missing=", ".join(missing))
        return {
            "result": False,
            "data": None,
            "error_reason": msg,
            "message": msg
        }

    return await _run_llm_analysis(resolved, query, runtime_state, tag="llm_analyze_multi_data")

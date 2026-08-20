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
    Résout une variable nommée (ex: '$@_data_file_content' ou 'data_file_content')
    en consultant le registre du solver courant dans runtime_state.
    Retourne la valeur brute de la variable, ou None si introuvable.
    """
    if var_name.startswith("$@_"):
        var_name = var_name[3:]

    # 1. PRIORITÉ : registre temporaire (défini par l'Executor)
    temp_registry = getattr(runtime_state, "_solver_registry_for_tools", None)
    if temp_registry is not None:
        info = temp_registry.get(var_name, {})
        # BUG CORRIGÉ (trouvé en écrivant les tests) : quand la valeur stockée
        # est brute (pas enveloppée dans {"value": ...}), `info` n'est pas un
        # dict — `"value" in info` testait alors une appartenance de
        # sous-chaîne (si info est une str) ou levait TypeError (si info est
        # un int/bool/etc.), au lieu de tester la présence d'une clé. Le garde
        # `isinstance` fait qu'on ne teste "value" que sur un vrai dict, et on
        # retombe correctement sur la valeur brute via le repli du dessous.
        if isinstance(info, dict) and "value" in info:
            return info["value"]
        if var_name in temp_registry:
            return temp_registry[var_name]
    return None


async def extract_json_value(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """Extrait une valeur d'un objet JSON à partir d'une clé ou d'un chemin."""
    data_var = args.get("data")
    key = args.get("key")
    path = args.get("path")

    if not data_var:
        return {"result": False, "data": None, "message": "Le paramètre 'data' est requis."}

    raw_value = await resolve_variable(data_var, runtime_state)
    if raw_value is None:
        return {"result": False, "data": None, "message": f"Variable '{data_var}' introuvable."}

    parsed = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {"result": False, "data": None, "message": "La variable ne contient pas un JSON valide."}

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
                return {"result": False, "data": None, "message": f"Chemin '{path}' non trouvé."}
        except Exception as e:
            return {"result": False, "data": None, "message": f"Erreur d'extraction : {str(e)}"}

    elif key:
        if isinstance(parsed, dict):
            if key in parsed:
                return {"result": True, "data": parsed[key], "message": "Extraction réussie."}
            else:
                return {"result": False, "data": None, "message": f"Clé '{key}' non trouvée."}
        else:
            return {"result": False, "data": None, "message": "La variable n'est pas un objet JSON."}

    return {"result": False, "data": None, "message": "Impossible d'extraire : fournissez 'key' ou 'path'."}


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
            "message": _("Aucun LLM disponible pour l'analyse.")
        }

    loader = get_prompt_loader()
    prompt = loader.load(
        "llm_analyze_data.md",
        lang=getattr(runtime_state, "language", "en"),
        data=data,
        query=query
    )

    try:
        analysis: AnalysisResult = await llm.generate_structured(
            prompt=prompt,
            schema=AnalysisResult,
            tag=tag
        )
        return {
            "result": analysis.success,
            "data": analysis.data,
            "message": analysis.message or _("Analyse terminée.")
        }
    except Exception as e:
        Logger.error(f"[{tag}] Erreur : {e}")
        return {
            "result": False,
            "data": None,
            "message": _("Erreur lors de l'analyse : {error}").format(error=str(e))
        }


async def llm_analyze_data(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Analyse une donnée (variable) à l'aide d'un LLM.
    
    Args:
        args (dict): Doit contenir "source" (nom de la variable) et "query" (question).
        runtime_state: L'état runtime (contient le LLM, le registre, etc.)
    
    Retourne:
        dict: {"result": bool, "data": Any, "message": str}
    """
    source = args.get("source")
    query = args.get("query")

    if not source or not query:
        return {
            "result": False,
            "data": None,
            "message": _("Les paramètres 'source' et 'query' sont requis.")
        }

    raw_value = await resolve_variable(source, runtime_state)
    if raw_value is None:
        return {
            "result": False,
            "data": None,
            "message": _("Variable '{source}' introuvable.").format(source=source)
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
        dict: {"result": bool, "data": Any, "message": str}
    """
    sources = args.get("sources")
    query = args.get("query")

    if not query:
        return {
            "result": False,
            "data": None,
            "message": _("Le paramètre 'query' est requis.")
        }

    if not sources or not isinstance(sources, list) or len(sources) < 2:
        return {
            "result": False,
            "data": None,
            "message": _(
                "Le paramètre 'sources' doit être une liste d'AU MOINS DEUX noms de "
                "variables. Pour une seule variable, utilisez plutôt 'llm_analyze_data'."
            )
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
        return {
            "result": False,
            "data": None,
            "message": _("Variable(s) introuvable(s) : {missing}").format(missing=", ".join(missing))
        }

    return await _run_llm_analysis(resolved, query, runtime_state, tag="llm_analyze_multi_data")
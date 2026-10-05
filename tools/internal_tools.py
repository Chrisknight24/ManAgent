"""
tools/internal_tools.py
=======================
Outils internes pour l'analyse de données structurées.
"""

import json
import re
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from core.prompt_loader import get_prompt_loader
try:
    from core.tools_models import AnalysisResult
except Exception:
    class AnalysisResult:
        pass
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

    MEDIA_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.pdf', '.mp4', '.mov', '.avi', '.mp3', '.wav', '.m4a')

    def _is_media_asset(asset_obj) -> bool:
        filename = getattr(asset_obj, "filename", "") or getattr(asset_obj, "filepath", "") or asset_obj.get_uri()
        if any(filename.lower().endswith(ext) for ext in MEDIA_EXTENSIONS):
            return True
        meta = getattr(asset_obj, "asset_meta", None)
        if meta and hasattr(meta, "mime_type"):
            mime = meta.mime_type.lower()
            if any(mime.startswith(prefix) for prefix in ["image/", "video/", "audio/", "application/pdf"]):
                return True
        return False

    def _format_asset_value(asset_obj):
        if _is_media_asset(asset_obj):
            fname = getattr(asset_obj, "filename", "") or getattr(asset_obj, "filepath", "") or asset_obj.get_uri()
            return f"[Asset Multimédia/Visuel : {asset_obj.get_uri()} (Fichier: {fname})]"
        return asset_obj.dump_data()

    if temp_registry is not None and isinstance(temp_registry, dict):
        # 1. Correspondance directe par nom de variable
        if var_name in temp_registry:
            info = temp_registry[var_name]
            if isinstance(info, dict):
                if info.get("type") == "asset" and "asset" in info:
                    return _format_asset_value(info["asset"])
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
                            return _format_asset_value(info["asset"])
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
            return _format_asset_value(asset)

    if raw_val is not None:
        return raw_val

    # 4. Repli ultime : chercher directement l'asset par son nom/URI dans l'AssetRegistry
    if asset_registry:
        asset = asset_registry.resolve_asset(var_name)
        if asset:
            return _format_asset_value(asset)

    return None


async def resolve_media_asset(var_name: str, runtime_state) -> Any:
    """
    Résout un asset média physique sous-jacent à partir d'un nom de variable.
    """
    if not var_name:
        return None

    if isinstance(var_name, str) and var_name.startswith("$@_"):
        var_name = var_name[3:]

    temp_registry = getattr(runtime_state, "_solver_registry_for_tools", None)
    raw_val = None
    source_uri = None

    if temp_registry is not None and isinstance(temp_registry, dict):
        if var_name in temp_registry:
            info = temp_registry[var_name]
            if isinstance(info, dict):
                if info.get("type") == "asset" and "asset" in info:
                    return info["asset"]
                source_uri = info.get("source_uri") or info.get("value")
                raw_val = info.get("value")
            else:
                raw_val = info

        if raw_val is None:
            for k, info in temp_registry.items():
                if isinstance(info, dict):
                    if info.get("source_uri") == var_name or info.get("value") == var_name:
                        if info.get("type") == "asset" and "asset" in info:
                            return info["asset"]
                        source_uri = info.get("source_uri") or info.get("value")
                        raw_val = info.get("value")
                        break

    candidate_uri = None
    for item in [raw_val, source_uri, var_name]:
        if isinstance(item, str) and any(item.startswith(p) for p in ["inputs://", "outputs://", "files://"]):
            candidate_uri = item
            break

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
            return asset

    # Tentative de résolution directe par URI
    if isinstance(var_name, str) and any(var_name.startswith(p) for p in ["inputs://", "outputs://", "files://"]) and asset_registry:
        asset = asset_registry.resolve_asset(var_name)
        if asset:
            return asset

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
    """Résout le LLM à utiliser pour une analyse interne."""
    llm = getattr(runtime_state, "_tools_llm", None)
    if not llm and hasattr(runtime_state, "tools_manager"):
        llm = getattr(runtime_state.tools_manager, "llm", None)
    if not llm and hasattr(runtime_state, "orchestrator"):
        llm = getattr(runtime_state.orchestrator, "llm", None)
    if not llm:
        llm = getattr(runtime_state, "current_llm", None)
    return llm


async def _run_llm_analysis(data: Any, query: str, runtime_state, tag: str, media_assets: Optional[List[Any]] = None) -> Dict[str, Any]:
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
    # En 2026, les modèles gèrent de larges contextes (Gemini, Claude, GPT, Groq, etc.)
    MAX_PROMPT_DATA_CHARS = 250000
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
        explicit_mid = None
        try:
            exec_ctx = getattr(runtime_state, "execution_context", None)
            if exec_ctx:
                explicit_mid = exec_ctx.get("mission_id")
        except Exception:
            explicit_mid = None
        analysis: AnalysisResult = await llm.generate_structured(
            prompt=prompt,
            schema=AnalysisResult,
            tag=tag,
            media_assets=media_assets,
            mission_id=explicit_mid
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
    Analyse une donnée (variable) à l'aide d'un LLM, avec support optionnel du découpage progressif.
    
    Args:
        args (dict): Doit contenir "source" (nom de la variable) et "query" (question).
                     Optionnels : "from_line", "to_line" pour le découpage progressif d'un texte/fichier.
        runtime_state: L'état runtime (contient le LLM, le registre, etc.)
    
    Retourne:
        dict: {"result": bool, "data": Any, "error_reason": str}
    """
    source = args.get("source")
    query = args.get("query")
    from_line = args.get("from_line")
    to_line = args.get("to_line")

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

    # Détection et traitement robuste des assets multimédias (images, documents PDF, etc.)
    asset = await resolve_media_asset(source, runtime_state)
    is_multimodal = False
    if asset:
        filename = getattr(asset, "filename", "") or getattr(asset, "filepath", "") or asset.get_uri()
        meta = getattr(asset, "asset_meta", None)
        mime = (getattr(meta, "mime_type", None) or "").lower()

        if mime:
            if mime.startswith("image/") or mime == "application/pdf":
                is_multimodal = True
        if not is_multimodal:
            import mimetypes
            guessed_mime, _unused = mimetypes.guess_type(filename)
            if guessed_mime and (guessed_mime.startswith("image/") or guessed_mime == "application/pdf"):
                is_multimodal = True
            elif any(filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.pdf']):
                is_multimodal = True

    if is_multimodal:
        llm = await _get_tools_llm(runtime_state)
        if not llm:
            msg = _("Aucun LLM disponible pour l'analyse.")
            return {
                "result": False,
                "data": None,
                "error_reason": msg,
                "message": msg
            }
        
        # Vérification de la capacité vision / multimodale
        from core.constants import ModelCapabilities
        has_cap = (
            llm.has_capability(ModelCapabilities.VISION)
            or (hasattr(ModelCapabilities, "MULTIMODAL") and llm.has_capability(ModelCapabilities.MULTIMODAL))
        )
        if not has_cap:
            model_id = getattr(llm, "model_id", "unknown")
            msg = f"DÉGRADATION GRACIEUSE : Le modèle actif '{model_id}' ne possède pas la capacité multimodale/vision requise pour analyser '{source}'."
            Logger.warning(f"[llm_analyze_data] {msg}")
            return {
                "result": False,
                "data": None,
                "error_reason": msg,
                "message": msg
            }

        Logger.info(f"[llm_analyze_data] Asset multimodal détecté '{source}', passage en mode analyse multimodale.")
        return await _run_llm_analysis(raw_value, query, runtime_state, tag="llm_analyze_data", media_assets=[asset])

    # Support Progressive Disclosure slicing (from_line, to_line)
    if (from_line is not None or to_line is not None) and isinstance(raw_value, str):
        lines = raw_value.splitlines()
        fl = max(1, int(from_line or 1)) - 1
        tl = int(to_line) if to_line is not None else len(lines)
        sliced_lines = lines[fl:tl]
        raw_value = "\n".join(sliced_lines)

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


async def execute_skill_tool(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Outil d'exécution d'un Skill composite ManAgent (Méta-Outil).
    Délègue l'exécution déterministe au SkillExecutionEngine.
    
    Args:
        args: {
            "skill_id": str,
            "parameters": dict (optionnel, arguments transmis au skill),
            "version": int (optionnel, défaut version active en production)
        }
        runtime_state: RuntimeState de l'agent.
    """
    skill_id = args.get("skill_id")
    parameters = args.get("parameters", {})
    version_num = args.get("version")

    if not skill_id:
        return {
            "result": False,
            "data": None,
            "error_reason": _("Le paramètre 'skill_id' est requis."),
            "message": _("Le paramètre 'skill_id' est requis.")
        }

    skill_registry = getattr(runtime_state, "skill_registry", None)
    if not skill_registry:
        from core.skills.registry import SkillRegistry
        skill_registry = SkillRegistry()

    manifest, version = skill_registry.get_active_skill(skill_id, target_version=version_num)
    if not manifest or not version:
        return {
            "result": False,
            "data": None,
            "error_reason": f"Skill '{skill_id}' non trouvé ou aucune version en production active.",
            "message": f"Skill '{skill_id}' non trouvé ou inactif."
        }

    from core.skills.models import SkillState
    if version.state != SkillState.PRODUCTION:
        return {
            "result": False,
            "data": None,
            "error_reason": f"Skill '{skill_id}' v{version.version} n'est pas en production (état actuel : {version.state.value}). Exécution refusée.",
            "message": f"Skill '{skill_id}' non disponible en production."
        }

    # Résolution des variables dans les paramètres (ex: $@_data_file)
    resolved_parameters = {}
    for k, v in parameters.items():
        if isinstance(v, str) and v.startswith("$@_"):
            var_name = v[3:]
            resolved_val = await resolve_variable(var_name, runtime_state)
            resolved_parameters[k] = resolved_val if resolved_val is not None else v
        else:
            resolved_parameters[k] = v

    # P6 — garde environnement : clés exigées absentes = refus net, jamais d'exécution.
    try:
        _hm = getattr(runtime_state, "host_manifest", None)
        _henv = None
        if isinstance(_hm, dict):
            _henv = _hm.get("environment") or {}
        elif _hm is not None:
            _henv = getattr(_hm, "environment", None) or {}
        if isinstance(_henv, dict) and _henv:
            _env_obj = manifest.environment if hasattr(manifest, "environment") else None
            if _env_obj is not None and hasattr(_env_obj, "is_compatible"):
                if not _env_obj.is_compatible(_henv):
                    _ok, _spec, _mis = _env_obj.calculate_compatibility(_henv)
                    return {
                        "result": False,
                        "data": None,
                        "error_reason": f"Skill '{skill_id}' incompatible avec cet hôte : {'; '.join(_mis[:3])}",
                        "message": f"Skill '{skill_id}' incompatible avec cet hôte.",
                    }
    except Exception:
        pass

    # Garde paramètres requis : manquant/vide/placeholder = refus net AVANT
    # tout appel hôte (jamais de @$_param_... envoyé à l'hôte). Averti en amont
    # (prompt + event skill_params_warning), tranché ici. Échec honnête compté.
    try:
        _schema = manifest.parameters_schema or {} if hasattr(manifest, "parameters_schema") else {}
        _required = list((_schema.get("required") or [])) if isinstance(_schema, dict) else []
        _lacking = []
        for _name in _required:
            _val = resolved_parameters.get(_name)
            if _val is None or (isinstance(_val, str) and not _val.strip()):
                _lacking.append(str(_name))
            elif isinstance(_val, str) and ("@$_param_" in _val or "$@_" in _val):
                _lacking.append(str(_name))
        if _lacking:
            from utils.logger import Logger as _Logger
            _Logger.warning(f"[execute_skill] Paramètres requis manquants pour '{skill_id}': {', '.join(_lacking)}.")
            try:
                _Logger.event(
                    "skill_params_refused",
                    skill_id=skill_id,
                    version=getattr(version, "version", None),
                    missing=_lacking,
                    reason=f"Paramètres requis non remplis : {', '.join(_lacking)}.",
                )
            except Exception:
                pass
            return {
                "result": False,
                "data": None,
                "error_reason": f"Skill '{skill_id}' : paramètres requis manquants ({', '.join(_lacking)}). Remplissez-les.",
                "message": f"Skill '{skill_id}' : paramètres requis manquants.",
            }
    except Exception:
        pass

    from core.skills.engine import SkillExecutionEngine
    event_emitter = getattr(runtime_state, "propagate_event", None)
    engine = SkillExecutionEngine(registry=skill_registry, event_emitter=event_emitter)

    mission_id = None
    if runtime_state and hasattr(runtime_state, "execution_context"):
        mission_id = runtime_state.execution_context.get("mission_id")

    # Récupération de l'exécuteur hôte depuis ToolsManager ou transport
    host_executor = getattr(runtime_state, "host_skill_executor", None)
    if not host_executor:
        # Fallback par défaut via tools_manager si l'hôte supporte les flux
        tools_mgr = getattr(runtime_state, "tools_manager", None)
        async def default_host_executor(payload_ref, params):
            if tools_mgr and hasattr(tools_mgr, "execute_flow"):
                return await tools_mgr.execute_flow(
                    payload_or_ref=payload_ref,
                    parameters=params,
                    skill_id=skill_id,
                    version=getattr(version, "version", None),
                    mission_id=mission_id
                )
            # P4 — pas de moteur de flux : REFUS NET, jamais de faux succès.
            # Un "ok" inventé empoisonnerait l'apprentissage (trust + leçons).
            from core.skills.models import FailureClass
            return {
                "success": False,
                "breakout": True,
                "failure_class": FailureClass.HOST_CAPABILITY_ERROR.value,
                "error_message": f"Hôte sans moteur de flux : skill '{skill_id}' inexécutable ici.",
            }
        host_executor = default_host_executor

    exec_result = await engine.execute_skill(
        manifest=manifest,
        version=version,
        parameters=resolved_parameters,
        host_executor=host_executor,
        is_shadow=False,
        mission_id=mission_id
    )

    is_success = exec_result.get("success", False)
    bo_rep = exec_result.get("breakout_report")
    bo_data = bo_rep.to_dict() if hasattr(bo_rep, "to_dict") else bo_rep

    fail_bun = exec_result.get("failure_bundle")
    fb_data = fail_bun.to_dict() if hasattr(fail_bun, "to_dict") else fail_bun

    return {
        "result": is_success,
        "data": exec_result.get("output", {}),
        "breakout": exec_result.get("breakout", False),
        "breakout_report": bo_data,
        "failure_bundle": fb_data,
        "passed_checkpoints": exec_result.get("passed_checkpoints", []),
        "executed_steps": exec_result.get("executed_steps", []),
        "error_reason": exec_result.get("error_message") or (None if is_success else "Échec d'exécution du skill"),
        "message": f"Skill '{skill_id}' exécuté avec succès ({len(exec_result.get('passed_checkpoints', []))} checkpoints)." if is_success else f"Rupture ou échec sur le Skill '{skill_id}'."
    }


# Noms d'outils internes : jamais utilisables comme source de perceive_understand
# (pas de LLM-dans-LLM, pas d'auto-appel).
_INTERNAL_TOOL_NAMES = frozenset({
    "extract_json_value", "llm_analyze_data", "llm_analyze_multi_data",
    "execute_skill", "tool_manager", "analyze_data", "load_literal_data",
    "perceive_understand", "perceive_action",
})


def _text(value: Any) -> str:
    """Texte sûr depuis un arg LLM (objet -> refus honnête en aval, jamais de crash `.strip()`)."""
    return value.strip() if isinstance(value, str) else ""


def _prevalidate_external(tools_mgr, tool_name: str, args) -> Optional[str]:
    """Vérifie les args AVANT l'appel hôte (schéma déclaré, agnostique).

    Retourne None si appelable, sinon le motif honnête (params requis).
    Outil inconnu ici = on laisse `execute_tool` répondre (comportement
    inchangé). Fonction pure de lecture, jamais de faux refus.
    """
    try:
        known = getattr(tools_mgr, "_tools", None) or {}
        if not (isinstance(known, dict) and tool_name in known):
            return None
        tools_mgr.validate_tool_call(tool_name, dict(args or {}))
        return None
    except ValueError as e:
        return str(e)
    except Exception:
        return None


async def perceive_understand(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Méta-outil de lecture : perçoit le monde via un outil EXTERNE (hôte),
    puis fait comprendre le résultat par LLM. Seule voie autorisée pour lire
    le monde (jamais d'appel direct à un outil de perception externe).

    Args:
        args: {
            "question": str (requis, langage naturel : que chercher/comprendre),
            "source_tool": str (outil externe hôte, ex déclaré au manifeste),
            "source_args": dict (optionnel, arguments de l'outil source),
            "source_data": str (optionnel, variable déjà disponible — alternative
                à source_tool, pas de nouvel appel monde dans ce cas),
            "format_response": str (optionnel, format strict attendu de la
                réponse, ex: "un identifiant", "un nombre", "oui/non".
                Vide = rapport libre.)
        }

    Retourne: dict {"result": bool, "data": Any, "error_reason": str}.
    """
    question = _text(args.get("question"))
    source_tool = _text(args.get("source_tool"))
    source_args = args.get("source_args") or {}
    source_data = _text(args.get("source_data"))
    format_response = _text(args.get("format_response"))

    if not question:
        msg = _("Le paramètre 'question' est requis.")
        return {"result": False, "data": None, "error_reason": msg, "message": msg}
    if not source_tool and not source_data:
        msg = _("'source_tool' ou 'source_data' requis (d'où lire le monde).")
        return {"result": False, "data": None, "error_reason": msg, "message": msg}

    raw_data = None
    if source_data:
        raw_data = await resolve_variable(source_data, runtime_state)
        if raw_data is None:
            msg = _("Variable '{source}' introuvable.").format(source=source_data)
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
    else:
        if source_tool in _INTERNAL_TOOL_NAMES:
            msg = _("'{tool}' est interne : la source doit être un outil externe (hôte).").format(tool=source_tool)
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
        tools_mgr = getattr(runtime_state, "tools_manager", None)
        if tools_mgr is None or not hasattr(tools_mgr, "execute_tool"):
            msg = _("Aucun gestionnaire d'outils pour appeler la source.")
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
        _bad = _prevalidate_external(tools_mgr, source_tool, source_args)
        if _bad:
            msg = _("La source '{tool}' est mal appelée : {err}").format(tool=source_tool, err=_bad)
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
        try:
            result_str = await tools_mgr.execute_tool(source_tool, dict(source_args))
            parsed = json.loads(result_str) if isinstance(result_str, str) else result_str
        except Exception as e:
            msg = _("La source '{tool}' a échoué : {err}").format(tool=source_tool, err=e)
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
        if isinstance(parsed, dict) and not parsed.get("result", True):
            msg = str(parsed.get("error_reason") or parsed.get("message") or _("Source en échec."))
            return {"result": False, "data": None, "error_reason": msg, "message": msg}
        raw_data = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
        # Charges typées déclarées (contrat `returns`) : les pixels passent
        # par le canal média, jamais en base64 dans le prompt. Sans
        # déclaration : repli texte actuel.
        media_assets = None
        try:
            _returns = tools_mgr.get_tool_returns(source_tool) if hasattr(tools_mgr, "get_tool_returns") else []
        except Exception:
            _returns = []
        if _returns and isinstance(raw_data, dict):
            try:
                from core.discovery.data_asset import extract_typed_payloads
                _payloads = extract_typed_payloads(raw_data, _returns, target_prefix=f"perceive_{source_tool}")
                if _payloads and hasattr(tools_mgr, "register_typed_assets"):
                    _uris = tools_mgr.register_typed_assets(_payloads)
                    media_assets = [a for a in _payloads if a.target_id in _uris]
                    if media_assets:
                        cleaned = dict(raw_data)
                        for _asset in media_assets:
                            _field = ""
                            try:
                                _field = (_asset.asset_meta.custom_attributes or {}).get("declared_field", "")
                            except Exception:
                                _field = ""
                            _parts = _field.split(".") if _field else []
                            _node = cleaned
                            for _p in _parts[:-1]:
                                if isinstance(_node, dict) and _p in _node:
                                    _node = _node[_p]
                                else:
                                    _node = None
                                    break
                            if isinstance(_node, dict) and _parts and _parts[-1] in _node:
                                _node[_parts[-1]] = (
                                    f"[Image : {_uris[_asset.target_id]} ({len(_asset.raw_bytes)} octets)]"
                                )
                        raw_data = cleaned
            except Exception as e:
                Logger.warning(f"[perceive_understand] Normalisation impossible ({e}) — repli texte.")
                media_assets = None

    query = question
    if format_response:
        query = (
            f"{question}\nRéponds UNIQUEMENT avec le format strict suivant : "
            f"{format_response}. Rien d'autre."
        )
    if media_assets:
        return await _run_llm_analysis(raw_data, query, runtime_state, tag="perceive_understand", media_assets=media_assets)
    return await _run_llm_analysis(raw_data, query, runtime_state, tag="perceive_understand")


class _GroundingResult(BaseModel):
    """Ancrage d'une cible perçue : quelle référence d'action exacte utiliser."""
    target_ref: str = Field(default="", description="Référence exacte à passer à l'outil d'action (id élément ou case), vide si incertain")
    reason: str = Field(default="", description="Pourquoi cette cible (1 phrase)")


async def perceive_action(args: Dict[str, Any], runtime_state) -> Dict[str, Any]:
    """
    Méta-outil : VOIT puis AGIT en une seule étape, sans écrire de référence
    dans le plan. La référence (id/case) vit quelques millisecondes dans
    l'appel, toujours fraîche — fini les "null", cellules quotées et refs
    périmées recopiées de mémoire.

    Args:
        args: {
            "question": str (requis : que trouver, ex "où est l'icône Edge ?"),
            "source_tool": str (requis : outil perception hôte),
            "source_args": dict (optionnel),
            "action_tool": str (requis : outil action hôte),
            "action_args": dict (optionnel : gabarit ; toute valeur EXACTEMENT
                égale à "$TARGET" reçoit la référence ancrée),
            "target_hint": str (optionnel : description de la cible),
        }

    Retourne: dict {"result": bool, "data": {"seen": ..., "done": ...}}.
    Ancrage incertain (pas de référence) = échec honnête, JAMAIS d'action
    aveugle.
    """
    question = _text(args.get("question"))
    source_tool = _text(args.get("source_tool"))
    source_args = args.get("source_args") or {}
    action_tool = _text(args.get("action_tool"))
    action_args = args.get("action_args") or {}
    target_hint = _text(args.get("target_hint"))

    def _fail(msg: str) -> Dict[str, Any]:
        return {"result": False, "data": None, "error_reason": msg, "message": msg}

    if not question:
        return _fail(_("Le paramètre 'question' est requis."))
    if not source_tool:
        return _fail(_("Le paramètre 'source_tool' est requis (quoi regarder)."))
    if not action_tool:
        return _fail(_("Le paramètre 'action_tool' est requis (quoi faire)."))
    if source_tool in _INTERNAL_TOOL_NAMES or action_tool in _INTERNAL_TOOL_NAMES:
        return _fail(_("source_tool et action_tool doivent être des outils externes (hôte)."))
    tools_mgr = getattr(runtime_state, "tools_manager", None)
    if tools_mgr is None or not hasattr(tools_mgr, "execute_tool"):
        return _fail(_("Aucun gestionnaire d'outils pour percevoir/agir."))

    # Pré-validation déterministe AVANT tout appel hôte (schémas déclarés,
    # agnostique). Évite l'aller-retour voué à l'échec (ex : `mode` manquant).
    for _tname, _targs, _role in (
        (source_tool, source_args, _("perception")),
        (action_tool, action_args, _("action")),
    ):
        _bad = _prevalidate_external(tools_mgr, _tname, _targs)
        if _bad:
            return _fail(_("L'outil {role} '{tool}' est mal appelé : {err}").format(
                role=_role, tool=_tname, err=_bad))

    # 1. Perception fraîche (jamais une référence stockée).
    try:
        seen_str = await tools_mgr.execute_tool(source_tool, dict(source_args))
        seen = json.loads(seen_str) if isinstance(seen_str, str) else seen_str
    except Exception as e:
        return _fail(_("La perception '{tool}' a échoué : {err}").format(tool=source_tool, err=e))
    if isinstance(seen, dict) and not seen.get("result", True):
        return _fail(str(seen.get("error_reason") or seen.get("message") or _("Perception en échec.")))

    # 2. Ancrage : quelle référence exacte pour l'action ?
    llm = await _get_tools_llm(runtime_state)
    if llm is None:
        return _fail(_("Aucun LLM disponible pour l'ancrage."))
    ground_prompt = (
        f"Cible cherchée : {target_hint or question}\n"
        f"Observation fraîche : {json.dumps(seen, ensure_ascii=False)[:4000]}\n"
        "Donne la référence EXACTE à passer à l'outil d'action (identifiant "
        "d'élément ou case de grille, recopiée telle quelle de l'observation). "
        "Si aucune cible sûre, laisse target_ref vide."
    )
    try:
        grounding = await llm.generate_structured(
            prompt=ground_prompt, schema=_GroundingResult, tag="perceive_action",
        )
    except Exception as e:
        return _fail(_("Ancrage impossible : {err}").format(err=e))
    target_ref = (getattr(grounding, "target_ref", "") or "").strip().strip("\"'")
    if not target_ref or "*" in target_ref or target_ref.lower() == "null":
        return _fail(_("Ancrage incertain : aucune cible sûre dans l'observation, pas d'action aveugle."))

    # 3. Action immédiate avec la référence fraîche.
    resolved_args = {}
    for key, val in (action_args if isinstance(action_args, dict) else {}).items():
        resolved_args[key] = target_ref if (isinstance(val, str) and val.strip() == "$TARGET") else val
    if not any(v == target_ref for v in resolved_args.values()):
        resolved_args = dict(resolved_args)
        resolved_args.setdefault("target", target_ref)
    try:
        done_str = await tools_mgr.execute_tool(action_tool, resolved_args)
        done = json.loads(done_str) if isinstance(done_str, str) else done_str
    except Exception as e:
        return _fail(_("L'action '{tool}' a échoué : {err}").format(tool=action_tool, err=e))
    if isinstance(done, dict) and not done.get("result", True):
        return _fail(str(done.get("error_reason") or done.get("message") or _("Action en échec.")))
    return {
        "result": True,
        "data": {"seen": seen if not isinstance(seen, dict) else seen.get("data", seen),
                 "done": done if not isinstance(done, dict) else done.get("data", done),
                 "target": target_ref},
        "message": _("Cible ancrée et action exécutée."),
    }

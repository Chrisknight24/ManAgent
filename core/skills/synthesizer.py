import json
import logging
import copy
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
try:
    from pydantic import model_validator
except ImportError:
    from pydantic import root_validator
    def model_validator(mode="before"):
        def decorator(func):
            return root_validator(pre=True, allow_reuse=True)(func)
        return decorator

from core.llm import Llm
from core.skills.models import SkillManifest, ExecutionEnvironment, Checkpoint, SkillState
from core.skills.registry import SkillRegistry
from utils.logger import Logger
from core.prompt_loader import PromptLoader

class DynamicParameterDef(BaseModel):
    type: str = Field(..., description="Type of the parameter (e.g., string, number, boolean)")
    description: str = Field(..., description="Description of the parameter")

class SynthesizedPlanNode(BaseModel):
    step_id: str
    type: str = "tool_call"
    tool_name: Optional[str] = None
    action: Optional[str] = None
    description: str
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionnaire complet et exhaustif de TOUS les arguments effectifs passés à l'outil (ex: {'action': 'send_hotkey', 'hotkey': 'win'}). Ne doit JAMAIS être vide si l'outil attend des paramètres."
    )
    expected_result: Optional[str] = "true"
    output_var: Optional[str] = None
    execute_if: Optional[str] = None

    @model_validator(mode="before")
    def _normalize_node_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalisation tool_args -> arguments
            if "arguments" not in data or data["arguments"] is None:
                data["arguments"] = data.get("tool_args") or data.get("args") or {}
            elif not isinstance(data["arguments"], dict):
                data["arguments"] = {}

            # Normalisation action -> arguments['action']
            action_val = data.get("action")
            if action_val and isinstance(action_val, str) and " " not in action_val and len(action_val) < 50:
                if "action" not in data["arguments"]:
                    data["arguments"]["action"] = action_val
        return data

class SkillSynthesisResult(BaseModel):
    description: str
    preconditions: List[str] = Field(default_factory=list, description="Conditions initiales nécessaires avant d'exécuter le Skill")
    postconditions: List[str] = Field(default_factory=list, description="Garanties causales obtenues après l'exécution du Skill")
    dynamic_parameters: Dict[str, DynamicParameterDef] = Field(default_factory=dict)
    meta_plan: List[SynthesizedPlanNode] = Field(default_factory=list)


def sanitize_for_serialization(obj: Any, seen: Optional[set] = None) -> Any:
    """
    Nettoie récursivement un objet Python pour éliminer les références circulaires
    et garantir une sérialisation JSON / Jinja2 100% stable.
    """
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return "[Circular Reference]"

    if isinstance(obj, dict):
        seen.add(obj_id)
        clean_dict = {}
        for k, v in obj.items():
            if str(k) in ("tool_args", "arguments", "args") and v is obj:
                continue
            clean_dict[str(k)] = sanitize_for_serialization(v, seen)
        seen.remove(obj_id)
        return clean_dict
    elif isinstance(obj, (list, tuple, set)):
        seen.add(obj_id)
        clean_list = [sanitize_for_serialization(item, seen) for item in obj]
        seen.remove(obj_id)
        return clean_list
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    else:
        return str(obj)


def extract_golden_traces(trees: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Extrait les traces dorées (Golden Traces) épurées à partir des arbres d'exécution récents.
    Filtre les tentatives échouées et ne conserve que la séquence linéaire des étapes réussies
    avec leurs outils et leurs arguments exacts, isolés de tout effet de bord.
    """
    golden_runs = []
    for tree in trees:
        if not isinstance(tree, dict):
            if hasattr(tree, "model_dump"):
                tree = tree.model_dump(mode="json")
            elif hasattr(tree, "to_dict"):
                tree = tree.to_dict()
            else:
                continue

        attempts = tree.get("attempts", [])
        successful_attempt = None
        for att in attempts:
            if att.get("outcome") == "success" or att.get("raw_tool_success") is True:
                successful_attempt = att
                break
        if not successful_attempt and attempts:
            successful_attempt = attempts[-1]

        raw_nodes = []
        if successful_attempt:
            raw_nodes = successful_attempt.get("nodes", [])
        elif "nodes" in tree:
            raw_nodes = tree.get("nodes", [])

        nodes = []
        for n in raw_nodes:
            if not isinstance(n, dict):
                n_dict = n.model_dump(mode="json") if hasattr(n, "model_dump") else (n.to_dict() if hasattr(n, "to_dict") else dict(n))
            else:
                n_dict = dict(n)

            status = n_dict.get("status", "")
            raw_success = n_dict.get("raw_tool_success")
            if status == "failed" or raw_success is False:
                continue

            tool_name = n_dict.get("tool_name") or n_dict.get("action")

            # Extraction propre des arguments : conteneur dédié canonique
            extracted_args = {}
            raw_args = n_dict.get("tool_args") or n_dict.get("arguments") or n_dict.get("args")
            if raw_args is None and n_dict.get("tool_args_json"):
                try:
                    raw_args = json.loads(n_dict["tool_args_json"])
                except Exception:
                    raw_args = {}

            if isinstance(raw_args, dict):
                for k, v in raw_args.items():
                    # Éviter l'auto-référence ou la réinjection de sous-champs système
                    if k not in ("tool_args", "arguments", "args") and v is not raw_args:
                        extracted_args[str(k)] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v

            # Normalisation : si l'action de l'outil est spécifiée au niveau nœud
            action_field = n_dict.get("action")
            if action_field and isinstance(action_field, str) and "action" not in extracted_args:
                if " " not in action_field and len(action_field) < 50:
                    extracted_args["action"] = action_field

            clean_tool_args = sanitize_for_serialization(extracted_args)

            nodes.append({
                "step_id": n_dict.get("step_id", ""),
                "tool_name": tool_name,
                "action": action_field or tool_name,
                "description": n_dict.get("description", ""),
                "tool_args": clean_tool_args,
                "status": status or "success"
            })

        if nodes:
            golden_runs.append(nodes)

    return golden_runs


def reconcile_synthesized_meta_plan(
    meta_plan: List[SynthesizedPlanNode],
    golden_runs: List[List[Dict[str, Any]]],
    tools_schema_map: Optional[Dict[str, Dict[str, Any]]] = None
) -> List[SynthesizedPlanNode]:
    """
    Réconcilie de manière agnostique et robuste les étapes du méta-plan synthétisé
    avec les arguments réels observés dans les traces dorées et les schémas d'outils.
    Garantit qu'aucun argument obligatoire ou déterminant n'est omis.
    """
    if not golden_runs or not meta_plan:
        return meta_plan

    primary_golden = max(golden_runs, key=len)

    # Regrouper les étapes dorées par outil pour un matching séquentiel précis
    golden_by_tool: Dict[str, List[Dict[str, Any]]] = {}
    for g_step in primary_golden:
        t_name = str(g_step.get("tool_name") or "").strip().lower()
        if t_name:
            golden_by_tool.setdefault(t_name, []).append(g_step)

    tool_usage_counter: Dict[str, int] = {}

    for idx, node in enumerate(meta_plan):
        t_name = str(node.tool_name or node.action or "").strip().lower()
        if not node.tool_name and t_name:
            node.tool_name = t_name

        if not isinstance(node.arguments, dict):
            node.arguments = {}

        # Assurer que si node.action est une sous-action valide, elle est dans arguments
        if node.action and isinstance(node.action, str) and node.action != node.tool_name:
            if " " not in node.action and len(node.action) < 50:
                if "action" not in node.arguments:
                    node.arguments["action"] = node.action

        # Trouver le step correspondant dans la trace dorée
        cand_list = golden_by_tool.get(t_name, [])
        usage_idx = tool_usage_counter.get(t_name, 0)
        matching_golden = None
        if usage_idx < len(cand_list):
            matching_golden = cand_list[usage_idx]
        elif cand_list:
            matching_golden = cand_list[-1]
        elif idx < len(primary_golden):
            matching_golden = primary_golden[idx]

        tool_usage_counter[t_name] = usage_idx + 1

        if matching_golden:
            g_args = matching_golden.get("tool_args", {})
            if isinstance(g_args, dict):
                for k, v in g_args.items():
                    if k in ("tool_args", "arguments", "args"):
                        continue
                    # Si la clé est absente ou vide dans node.arguments, la restaurer proprement
                    if k not in node.arguments or node.arguments[k] in (None, "", {}):
                        node.arguments[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
                        Logger.info(
                            f"[SkillSynthesizer] 🔄 Réconciliation argument '{k}'={v} pour step {node.step_id} ({t_name}) depuis la trace dorée."
                        )

        # Nettoyage systématique pour éliminer tout résidu circulaire éventuel
        node.arguments = sanitize_for_serialization(node.arguments)

        # Application agnostique des valeurs par défaut déclarées dans les schémas d'outils
        if tools_schema_map and t_name in tools_schema_map:
            t_schema = tools_schema_map[t_name].get("parameters", {}) if isinstance(tools_schema_map[t_name], dict) else {}
            t_props = t_schema.get("properties", {}) if isinstance(t_schema, dict) else {}
            for prop_name, prop_def in t_props.items():
                if prop_name not in node.arguments and isinstance(prop_def, dict) and "default" in prop_def:
                    node.arguments[prop_name] = prop_def["default"]

        Logger.debug(f"[SkillSynthesizer] Step {node.step_id} ({t_name}) final arguments: {node.arguments}")

    return meta_plan

class SkillSynthesizer:
    """
    Phase 6: Synthesize a meta-plan from a history of successful execution trees.
    """
    def __init__(self, llm: Llm, prompt_loader: Optional[PromptLoader] = None):
        self.llm = llm
        self.prompt_loader = prompt_loader or PromptLoader()
        self.registry = SkillRegistry()

    async def synthesize(
        self,
        skill_id: str,
        combined_signature: str,
        primary_action: str,
        primary_object: str,
        recent_trees: List[Dict[str, Any]],
        mission_id: Optional[str] = None,
    ) -> Optional[SkillManifest]:
        """
        Génère un SkillManifest en condensant les récents arbres d'exécution,
        puis l'enregistre en DRAFT puis SHADOW.
        """
        if len(recent_trees) == 0:
            Logger.warning(f"[SkillSynthesizer] Aucun arbre fourni pour la synthèse de {skill_id}.")
            return None

        # Extraction dynamique des schémas d'outils indexés par nom
        tools_schema_map: Dict[str, Dict[str, Any]] = {}
        tools_view = ""
        if hasattr(self.llm, "runtime_state") and self.llm.runtime_state:
            tools_mgr = getattr(self.llm.runtime_state, "tools_manager", None)
            if tools_mgr and hasattr(tools_mgr, "get_tools_view"):
                try:
                    tools_raw = await tools_mgr.get_tools_view()
                    if isinstance(tools_raw, list):
                        for t in tools_raw:
                            if isinstance(t, dict) and t.get("name"):
                                tools_schema_map[t["name"]] = t
                    tools_view = json.dumps(tools_raw, indent=2, ensure_ascii=False) if isinstance(tools_raw, (list, dict)) else str(tools_raw)
                except Exception as e:
                    Logger.debug(f"[SkillSynthesizer] Impossible de récupérer tools_view : {e}")

        # Extraction des traces dorées (Golden Traces) épurées
        golden_runs = extract_golden_traces(recent_trees)

        # Récupération dynamique des compétences (skills) existantes dans le registre
        skills_view = ""
        try:
            all_skills = self.registry.list_all_skills()
            active_skills = [
                {
                    "skill_id": s.get("skill_id"),
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "state": s.get("state"),
                    "version": s.get("active_version") or s.get("version")
                }
                for s in all_skills if s.get("state") in ["PRODUCTION", "SHADOW"]
            ]
            skills_view = json.dumps(active_skills, indent=2, ensure_ascii=False) if active_skills else "Aucune autre compétence active."
        except Exception as e:
            Logger.debug(f"[SkillSynthesizer] Impossible de récupérer les skills existants : {e}")
            skills_view = "Non disponibles."

        prompt = self.prompt_loader.load(
            "skill_synthesis.md",
            combined_signature=combined_signature,
            trees=recent_trees,
            golden_traces=golden_runs,
            tools=tools_view or "Non disponibles.",
            skills=skills_view
        )

        # Context scope pour l'observabilité hiérarchique (Logs, EventStream, Dashboard)
        exec_ctx = getattr(self.llm.runtime_state, "execution_context", None) if hasattr(self.llm, "runtime_state") else None
        scope_ctx = (
            exec_ctx.scope(
                entity_id=f"skill_synthesizer_{skill_id}",
                entity_name=f"Skill Synthesizer ({skill_id})",
                entity_role="skill_synthesizer",
                skill_id=skill_id
            )
            if exec_ctx is not None
            else None
        )

        try:
            Logger.info(f"[SkillSynthesizer] 🧠 Démarrage de la synthèse (LLM) pour {skill_id}...")
            if scope_ctx:
                with scope_ctx:
                    synthesis: SkillSynthesisResult = await self.llm.generate_structured(
                        prompt=prompt,
                        schema=SkillSynthesisResult,
                        tag="SkillSynthesis",
                        mission_id=mission_id,
                    )
            else:
                synthesis: SkillSynthesisResult = await self.llm.generate_structured(
                    prompt=prompt,
                    schema=SkillSynthesisResult,
                    tag="SkillSynthesis",
                    mission_id=mission_id,
                )
            Logger.info(f"[SkillSynthesizer] ✅ Synthèse réussie pour {skill_id}.")
        except Exception as e:
            Logger.error(f"[SkillSynthesizer] Échec de la synthèse LLM pour {skill_id} : {e}")
            return None

        # Fallback automatique sur la trace dorée si le LLM n'a généré aucun nœud
        if not synthesis.meta_plan and golden_runs:
            Logger.warning(f"[SkillSynthesizer] Aucun nœud généré par le LLM pour {skill_id}. Fallback direct sur la trace dorée.")
            primary_golden = max(golden_runs, key=len)
            synthesis.meta_plan = [
                SynthesizedPlanNode(
                    step_id=f"step_{idx + 1}",
                    type="tool_call",
                    tool_name=g.get("tool_name"),
                    action=g.get("action") or g.get("tool_name"),
                    description=g.get("description") or f"Action {g.get('tool_name')}",
                    arguments=dict(g.get("tool_args") or {}),
                    expected_result="true"
                )
                for idx, g in enumerate(primary_golden)
            ]

        # Réconciliation déterministe et agnostique des arguments avec les traces dorées
        synthesis.meta_plan = reconcile_synthesized_meta_plan(
            meta_plan=synthesis.meta_plan,
            golden_runs=golden_runs,
            tools_schema_map=tools_schema_map
        )

        # Construction du JSON Schema des paramètres d'entrée
        parameters_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        for param_name, param_def in synthesis.dynamic_parameters.items():
            parameters_schema["properties"][param_name] = {
                "type": param_def.type,
                "description": param_def.description
            }
            parameters_schema["required"].append(param_name)

        # Construction du SkillManifest avec ses contraintes d'environnement, préconditions et postconditions
        preconds = [{"description": p} if isinstance(p, str) else p for p in (synthesis.preconditions or [])]
        postconds = [{"description": p} if isinstance(p, str) else p for p in (synthesis.postconditions or [])]
        
        # Remplissage des contraintes d'environnement de manière 100% agnostique depuis l'hôte
        env_reqs: Dict[str, Any] = {}
        variant_tag = "DEFAULT"
        namespace = "general"
        if hasattr(self.llm, "runtime_state") and self.llm.runtime_state:
            host_m = getattr(self.llm.runtime_state, "host_manifest", None)
            if host_m:
                h_dict = host_m.to_dict() if hasattr(host_m, "to_dict") else dict(host_m)
                if isinstance(h_dict, dict):
                    namespace = h_dict.get("namespace") or h_dict.get("host_name", "general")
                    if isinstance(h_dict.get("environment"), dict):
                        env_reqs.update(h_dict["environment"])
                    if isinstance(h_dict.get("metadata"), dict):
                        env_reqs.update(h_dict["metadata"])
                    variant_tag = str(env_reqs.get("variant") or env_reqs.get("variant_tag") or h_dict.get("variant_tag") or "DEFAULT")

        exec_env = ExecutionEnvironment(
            requirements=env_reqs,
            variant_tag=variant_tag,
            preconditions=preconds,
            postconditions=postconds
        )

        manifest = SkillManifest(
            skill_id=skill_id,
            namespace=namespace,
            name=f"{primary_action.capitalize()} {primary_object}",
            description=synthesis.description,
            parameters_schema=parameters_schema,
            signature_hashes=[f"sig:{primary_action}:{primary_object}"],
            environment=exec_env,
            target_applications=[primary_object] if primary_object else [],
            preconditions=preconds,
            postconditions=postconds
        )

        # Flow Payload contient l'action de base + le méta-plan
        raw_meta_plan = [node.model_dump(exclude_none=True) if hasattr(node, "model_dump") else node.dict(exclude_none=True) for node in synthesis.meta_plan]
        flow_payload = sanitize_for_serialization({
            "action": primary_action,
            "object": primary_object,
            "meta_plan": raw_meta_plan
        })

        # Extraction de la carte d'identité du modèle créateur
        model_id = getattr(self.llm, "model_id", "unknown")
        capabilities = []
        reasoning_score = 1.0
        benchmark_score = 50.0

        if hasattr(self.llm, "runtime_state") and self.llm.runtime_state:
            prov_mgr = getattr(self.llm.runtime_state, "provider_manager", None)
            if prov_mgr and hasattr(prov_mgr, "get_model_metadata"):
                meta = prov_mgr.get_model_metadata(model_id)
                if meta:
                    capabilities = meta.capabilities
                    reasoning_score = meta.reasoning_score
                    benchmark_score = meta.benchmark_score

        # Enregistrement dans la base (supporte création v1 ou nouvelle version vN+1 suite à re-synthèse)
        created_ver = self.registry.create_synthesis_version(
            manifest=manifest,
            flow_payload=flow_payload,
            creator_model=model_id,
            creator_capabilities=capabilities,
            min_reasoning_score=reasoning_score,
            min_benchmark_score=benchmark_score,
            initial_state=SkillState.SHADOW
        )
        new_version_num = created_ver.version

        self.registry.transition_state(
            skill_id=skill_id,
            version=new_version_num,
            target_state=SkillState.SHADOW,
            reason=f"Skill v{new_version_num} auto-synthétisé via SkillSynthesizer et passé en SHADOW."
        )

        self.registry.index_signature(f"sig:{primary_action}:{primary_object}", skill_id, target_app=primary_object)
        
        Logger.event(
            "skill_created",
            skill_id=skill_id,
            version=new_version_num,
            state=SkillState.SHADOW.value,
            primary_action=primary_action,
            primary_object=primary_object,
            description=manifest.description,
            meta_plan_steps=len(flow_payload.get("meta_plan", [])),
            creator_model=model_id
        )

        Logger.info(f"[SkillSynthesizer] 🚀 Skill '{skill_id}' v{new_version_num} généré avec succès en SHADOW !")
        return manifest

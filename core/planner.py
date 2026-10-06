"""
planner.py
==========
Composant d'ingénierie de plan (Stateless).
Hérite désormais de Entity pour bénéficier de l'ID unique et des DataProviders.
Version corrigée : activation explicite de la Progressive Disclosure.
"""

import asyncio
import difflib
import json
from pydantic import ValidationError
from .plan_models import Plan, PlanStep, StepType
from core.llm import Llm
from utils.logger import Logger
import re
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.constants import Events, ModelCapabilities, LLM_STRUCTURED_MAX_OUTPUT_TOKENS
from typing import Tuple, List, Optional, Any, Dict, Union
from core.entity import Entity

PLANNER_PD_ENABLED = False


class PlannerRegistryProvider:
    """
    DataProvider pour le Planner – interroge le registre du Solver parent
    via le runtime_state.execution_context.
    """

    def __init__(self, planner: 'Planner'):
        self.planner = planner

    def get_data_type(self) -> str:
        return "registry"

    def get_targets(self) -> List[str]:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return []
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        return list(registry.keys()) if registry else []

    def get_data(self, target: str) -> Any:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return None
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        return registry.get(target, {}).get("value")

    def get_metadata(self, target: str) -> Dict[str, Any]:
        ctx = self.planner.runtime_state.execution_context
        solver_id = ctx.get("solver_id")
        if not solver_id:
            return {}
        registry = self.planner.runtime_state.solver_registry.get(solver_id, {}).get("variable_registry", {})
        info = registry.get(target, {})
        return {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A")
        }


class Planner(Entity):
    """
    Planner – construit des plans d'action.
    Hérite de Entity.
    """

    def __init__(
        self,
        name: str,
        llm: Llm,
        runtime_state,
        parent: Optional[Entity] = None
    ):
        super().__init__(name=name, role="planner", llm=llm, parent=parent)
        self.runtime_state = runtime_state
        self._cached_advice: Optional[str] = None
        self._last_proposed_plan: Optional[Plan] = None

        if self.llm and (not hasattr(self.llm, "requirement") or self.llm.requirement.role_name == "general"):
            from providers.provider_manager import ModelRequirement
            self.llm.requirement = ModelRequirement.for_planner(
                preferred_provider=self.llm.provider_id if self.llm.provider_id != "auto" else None,
                preferred_model=self.llm.model_id if self.llm.model_id != "auto" else None
            )

        if self.runtime_state.discovery_engine:
            registry_provider = PlannerRegistryProvider(self)
            self.register_data_provider("registry", registry_provider)
            Logger.info(
                _("[Planner:{name}] DataProvider 'registry' enregistré (via contexte d'exécution).")
                .format(name=self.name)
            )

            if PLANNER_PD_ENABLED:
                if not self.llm._discovery_enabled:
                    self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                    Logger.info(
                        _("[Planner:{name}] Progressive Disclosure activée.")
                        .format(name=self.name)
                    )
            else:
                Logger.debug(
                    f"[Planner:{self.name}] Progressive Disclosure désactivée pour cette entité "
                    f"(PLANNER_PD_ENABLED=False)."
                )

    def get_data_providers(self) -> Dict[str, Any]:
        """Retourne les DataProviders du Planner (exclut rigoureusement 'skills')."""
        providers = super().get_data_providers()
        providers.pop("skills", None)
        return providers

    async def process(self, *args, **kwargs) -> Plan:
        goal = kwargs.get("goal", args[0] if args else "")
        context = kwargs.get("context", args[1] if len(args) > 1 else "")
        strategy = kwargs.get("strategy", args[2] if len(args) > 2 else "")
        variable_registry = kwargs.get("variable_registry", args[3] if len(args) > 3 else {})
        candidate_skills = kwargs.get("candidate_skills", args[4] if len(args) > 4 else None)
        enable_world_pd = kwargs.get("enable_world_pd", args[5] if len(args) > 5 else False)
        return await self.propose_plan(goal, context, strategy, variable_registry, candidate_skills=candidate_skills, enable_world_pd=enable_world_pd)

    async def propose_plan(
        self,
        goal: str,
        context: str,
        strategy: str,
        variable_registry: dict,
        candidate_skills: Optional[Union[List[Dict[str, Any]], str]] = None,
        enable_world_pd: bool = False,
        previous_failures: str = "",
    ) -> Plan:
        if hasattr(self.runtime_state, 'orchestrator') and self.runtime_state.orchestrator:
            await self.runtime_state.orchestrator.propagate_event(Events.STATUS_UPDATE, {"message": "planning"})
        Logger.info("[Planner] 🧠 Traduction de la stratégie en plan d'action structuré...")

        fallback_msg = "Aucun conseil historique ou sémantique pertinent disponible pour cette tâche."
        if self._cached_advice is None:
            self._cached_advice = fallback_msg
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    advice_result = await self.runtime_state.learner.get_advice(
                        entity_types=["Planner", "Executor"], goal=goal
                    )
                    if advice_result:
                        self._cached_advice = advice_result
                except Exception as e:
                    Logger.error(f"[Planner] Erreur récupération des conseils : {e}")
                    self._cached_advice = fallback_msg
        advice = self._cached_advice
        if advice:
            Logger.info(f"[Planner] 💡 Conseil injecté ({len(advice)} caractères).")

        tools_view = await self.runtime_state.tools_manager.get_tools_view()
        try:
            from core.alignment import world_guidance_visible
            show_world_guidance = world_guidance_visible(tools_view)
        except Exception:
            show_world_guidance = True
        # Monde vivant : UNIQUEMENT en retry (premier passage inchangé :
        # rapide, pas cher). Le solver active après le 1er échec.
        if enable_world_pd and self.runtime_state.discovery_engine:
            try:
                from core.discovery.providers.world_provider import WorldProvider
                self.register_data_provider("world", WorldProvider(self.runtime_state))
                if self.llm and not self.llm._discovery_enabled:
                    self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                Logger.info(
                    _("[Planner:{name}] Monde vivant exposé (retry seul).")
                    .format(name=self.name)
                )
            except Exception as e:
                Logger.warning(f"[Planner:{self.name}] World PD indisponible : {e}")
        loader = get_prompt_loader()

        skills_text = ""
        if candidate_skills:
            if isinstance(candidate_skills, str):
                skills_text = candidate_skills
            elif isinstance(candidate_skills, list):
                lines = []
                for idx, sk in enumerate(candidate_skills, 1):
                    if isinstance(sk, dict):
                        s_id = sk.get("skill_id", "")
                        desc = sk.get("description", "")
                        ver = sk.get("version", 1)
                        trust = sk.get("trust_score", 1.0)
                        preconds = sk.get("preconditions", [])
                        postconds = sk.get("postconditions", [])
                        
                        lines.append(f"{idx}. Skill ID : `{s_id}` (v{ver}, Score Confiance : {trust:.2f})")
                        lines.append(f"   Description : {desc}")
                        if preconds:
                            pre_str = ", ".join([str(p.get("description") if isinstance(p, dict) else p) for p in preconds])
                            lines.append(f"   ⚡ Préconditions : {pre_str}")
                        if postconds:
                            post_str = ", ".join([str(p.get("description") if isinstance(p, dict) else p) for p in postconds])
                            lines.append(f"   🎯 Postconditions : {post_str}")
                    else:
                        lines.append(f"{idx}. {sk}")
                skills_text = "\n".join(lines)

        enriched_registry = {}
        for name, info in (variable_registry or {}).items():
            enriched_registry[name] = {
                "description": info.get("description", ""),
                "timestamp": info.get("timestamp", "N/A"),
                "source": info.get("source", "N/A"),
            }

        # --- PRÉPARATION DES MODALITÉS GÉRÉES / DYNAMIQUES POUR LE PLANIFICATEUR ---
        supported_modalities = []
        unsupported_modalities = []
        
        modalities_def = {
            "audio": {
                "capability": ModelCapabilities.AUDIO,
                "formats": ["MP3", "WAV", "OGG", "M4A", "FLAC"],
                "name": "audio (fichiers audio, voix)",
            },
            "video": {
                "capability": ModelCapabilities.VIDEO,
                "formats": ["MP4", "MKV", "AVI", "MOV"],
                "name": "vidéo (fichiers vidéo, flux)",
            },
            "vision": {
                "capability": ModelCapabilities.VISION,
                "formats": ["PNG", "JPG", "JPEG", "WEBP", "GIF"],
                "name": "vision (images, photos, captures d'écran)",
            }
        }
        
        for mod_key, mod_info in modalities_def.items():
            has_cap = False
            if self.llm.provider_manager.get_model_metadata(self.llm.model_id) is not None:
                has_cap = self.llm.provider_manager.has_model_capability(self.llm.model_id, mod_info["capability"])
            
            info_dict = {
                "name": mod_info["name"],
                "formats": ", ".join(mod_info["formats"])
            }
            if has_cap:
                supported_modalities.append(info_dict)
            else:
                unsupported_modalities.append(info_dict)

        prompt = loader.load(
            "planner.md",
            lang=self.runtime_state.language,
            goal=goal,
            context=context,
            strategy=strategy,
            previous_failures=previous_failures or "",
            world_guidance=show_world_guidance,
            variable_registry=enriched_registry,
            tools=tools_view,
            skills=skills_text,
            advice=advice,
            model_id=self.llm.model_id,
            supported_modalities=supported_modalities,
            unsupported_modalities=unsupported_modalities
        )

        proposed_plan: Plan = await self.llm.generate_structured(
            prompt=prompt,
            schema=Plan,
            tag="Plan",
            with_discovery=enable_world_pd,
            max_output_tokens=LLM_STRUCTURED_MAX_OUTPUT_TOKENS
        )

        try:
            plan_json = proposed_plan.model_dump_json(indent=2)
            Logger.debug(f"[Planner] Plan généré :\n{plan_json}")
        except Exception as e:
            Logger.warning(f"[Planner] Impossible de logger le plan : {e}")

        self._last_proposed_plan = proposed_plan
        if not proposed_plan.steps:
            raise ValueError(_("Le plan généré par le LLM est structurellement valide mais ne contient aucune étape."))

        Logger.info(f"[Planner] ✅ Plan structuré reçu avec {len(proposed_plan.steps)} étapes.")

        is_valid, warnings = self._validate_plan(proposed_plan, variable_registry)
        if not is_valid:
            raise ValueError(_("Plan invalide :\n") + "\n".join(warnings))
        elif warnings:
            Logger.warning(f"[Planner] ⚠️ Plan valide avec warnings : {', '.join(warnings)}")

        return proposed_plan

    def _validate_plan(self, plan: Plan, variable_registry: dict = None) -> Tuple[bool, List[str]]:
        known_vars = set(variable_registry.keys()) if variable_registry else set()
        # Normalisation symétrique pour les variables héritées
        for var in list(known_vars):
            if var.startswith("bool_"):
                known_vars.add("data_" + var[5:])
            elif var.startswith("data_"):
                known_vars.add("bool_" + var[5:])

        errors = []
        warnings = []
        all_steps_by_id = {step.id: step for step in plan.steps}
        steps_seen_so_far = set()
        # Pistes de correction : (variable demandée, variables connues à ce point).
        # Le feedback doit montrer la sortie, pas seulement l'erreur, sinon un
        # modèle faible rejoue la même variable inconnue en boucle.
        unknown_hints = []

        def _produced_names(st) -> set:
            """Noms produits par une étape (pour exempter sa propre prose)."""
            names = {f"bool_{st.id}", f"data_{st.id}"}
            if getattr(st, "output_variable_name", None):
                out_name = st.output_variable_name
                clean = re.sub(r'^(bool_|data_)+', '', out_name)
                names.update({out_name, clean, f"bool_{clean}", f"data_{clean}"})
            return names

        def _collect_used(*fields) -> set:
            used = set()
            for field in fields:
                if field:
                    matches = re.findall(r'(?:\$@_|@\$_)([a-zA-Z0-9_]+)', str(field))
                    used.update(matches)
            return used

        def _check_var(raw_var: str, known: set, step, step_idx: int, is_prose: bool = False) -> None:
            # Doctrine : exécutable (execute_if, args) = rigide (erreur, ça planterait).
            # Prose (description, response_text, step_context) = souple (avertissement
            # seul, jamais un rejet — un rejet coûte une tentative pour un doute de style).
            dest = warnings if is_prose else errors
            clean_root = re.sub(r'^(bool_|data_)+', '', raw_var)
            is_bool_ref = raw_var.startswith("bool_")
            is_data_ref = raw_var.startswith("data_")
            canonical_var = f"bool_{clean_root}" if is_bool_ref else (f"data_{clean_root}" if is_data_ref else raw_var)
            var = canonical_var

            is_available = False
            if raw_var in known or canonical_var in known:
                is_available = True
            elif f"bool_{clean_root}" in known or f"data_{clean_root}" in known or clean_root in known:
                is_available = True
            elif clean_root in all_steps_by_id and clean_root in steps_seen_so_far:
                is_available = True

            if not is_available:
                # Vérifier si la variable fait référence à une étape connue
                matched_step_id = None
                if clean_root in all_steps_by_id:
                    matched_step_id = clean_root

                # Vérifier si elle correspond à l'output_variable_name d'une étape future
                future_step = None
                for future_idx in range(step_idx + 1, len(plan.steps)):
                    cand_step = plan.steps[future_idx]
                    if cand_step.output_variable_name:
                        out_name = cand_step.output_variable_name
                        cand_clean = re.sub(r'^(bool_|data_)+', '', out_name)
                        if clean_root == cand_clean or var == out_name:
                            future_step = cand_step
                            break

                if matched_step_id:
                    target_step = all_steps_by_id[matched_step_id]
                    if target_step.type == StepType.DIRECT_ANSWER:
                        dest.append(
                            _("L'étape '{}' tente d'utiliser la variable '{}' associée à l'étape '{}' de type 'direct_answer' (les réponses directes ne produisent pas de données pour d'autres étapes).")
                            .format(step.id, var, matched_step_id)
                        )
                    elif matched_step_id not in steps_seen_so_far:
                        dest.append(
                            _("L'étape '{}' tente d'utiliser la variable '{}' provenant de l'étape future '{}' (erreur de causalité : l'étape n'a pas encore été exécutée).")
                            .format(step.id, var, matched_step_id)
                        )
                    else:
                        dest.append(
                            _("L'étape '{}' tente d'utiliser la variable '{}' issue de l'étape '{}' qui n'a pas produit de données valides.")
                            .format(step.id, var, matched_step_id)
                        )
                elif future_step:
                    dest.append(
                        _("L'étape '{}' tente d'utiliser la variable '{}' issue de l'étape future '{}' (erreur de causalité temporelle).")
                        .format(step.id, var, future_step.id)
                    )
                else:
                    dest.append(
                        _("L'étape '{}' tente d'utiliser la variable inconnue '{}' qui n'a été produite par aucune étape antérieure ni par le contexte.")
                        .format(step.id, var)
                    )
                    if not is_prose:
                        unknown_hints.append((var, sorted(set(known))))

        for step_idx, step in enumerate(plan.steps):
            # 1. Champs exécutables : antériorités seules (strict, erreur = rejet).
            for raw_var in _collect_used(step.execute_if, step.tool_args_json):
                _check_var(raw_var, known_vars, step, step_idx, is_prose=False)
            # 1b. Prose : sa propre sortie = déclaration, pas usage. Souple :
            # variable inconnue dans le texte = avertissement seul, jamais un rejet.
            prose_known = set(known_vars) | _produced_names(step)
            for raw_var in _collect_used(step.response_text, step.step_context, step.description):
                _check_var(raw_var, prose_known, step, step_idx, is_prose=True)

            # 3. Enregistrer les variables produites par cette étape
            steps_seen_so_far.add(step.id)

            if step.type in [StepType.TOOL_CALL, StepType.ABSTRACT_TASK]:
                known_vars.add(f"bool_{step.id}")
                known_vars.add(f"data_{step.id}")

            if step.output_variable_name:
                out_name = step.output_variable_name
                clean_out_root = re.sub(r'^(bool_|data_)+', '', out_name)
                known_vars.add(out_name)
                known_vars.add(clean_out_root)
                known_vars.add(f"bool_{clean_out_root}")
                known_vars.add(f"data_{clean_out_root}")

            # 4. Vérifications d'intégrité tool_args_json
            if step.type == StepType.TOOL_CALL and step.tool_args_json:
                try:
                    args = json.loads(step.tool_args_json)
                    if isinstance(args, dict):
                        forbidden_keys = ["output_variable_name", "output_var", "var_name", "variable_name"]
                        for key in forbidden_keys:
                            if key in args:
                                errors.append(
                                    _("L'étape '{}' définit '{}' dans tool_args_json, ce qui est interdit. "
                                    "Utilisez uniquement le champ output_variable_name de l'étape pour nommer des variables.")
                                    .format(step.id, key)
                                )
                                break
                except (json.JSONDecodeError, TypeError):
                    pass

        if unknown_hints:
            # Une seule piste par variable : proches + disponibles (capé).
            # Sans ça, un modèle faible ne voit pas la sortie et rejoue pareil.
            seen_vars = set()
            for bad_var, known_list in unknown_hints:
                if bad_var in seen_vars:
                    continue
                seen_vars.add(bad_var)
                suggestions = difflib.get_close_matches(bad_var, known_list, n=2, cutoff=0.6)
                available = ", ".join(f"$@_{k}" for k in known_list[:12])
                hint = _("Variables disponibles à ce point : {available}.").format(
                    available=available or _("(aucune — produisez d'abord une étape)"))
                if suggestions:
                    hint += " " + _("Vouliez-vous dire {guess} ?").format(
                        guess=" ou ".join(f"$@_{s}" for s in suggestions))
                errors.append(
                    _("Piste pour '{var}' : {hint}").format(var=bad_var, hint=hint))

        if not errors:
            return True, warnings
        return False, errors

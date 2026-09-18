"""
core/discovery/explorers/skills_explorer.py
==========================================

Explorer pour la bibliothèque de compétences (Skills).
Implémente intégralement l'interface BaseExplorer (outils, exécution,
génération de plan d'investigation, signatures normalisées).
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from core.discovery.base_explorer import BaseExplorer
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType, ExplorerStep
from core.runtime_state import RuntimeState
from core.skills.registry import SkillRegistry
from core.llm import Llm
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.constants import Events
from utils.logger import Logger
from core.discovery.data_provider import DataProvider


class ExplorerPlanOutput(BaseModel):
    """Structure de réponse attendue du LLM de l'Explorer."""
    steps: List[ExplorerStep] = Field(
        ...,
        description=_("Liste des étapes à exécuter pour atteindre l'objectif.")
    )


class SkillsExplorer(BaseExplorer):
    """
    Explorer pour les compétences (Skills).
    Permet d'explorer, inspecter les manifests, paramètres et méta-plans.
    """

    def __init__(self, runtime_state: RuntimeState, registry: Optional[SkillRegistry] = None, llm: Optional[Llm] = None):
        super().__init__(runtime_state)
        self._data_type = "skills"
        self.skill_registry = registry or SkillRegistry()
        self.llm = llm
        self._prompt_loader = get_prompt_loader()

    def get_data_type(self) -> str:
        return self._data_type

    def get_scope_description(self) -> str:
        return "Explore, inspecte et extrait les structures, paramètres, schémas et méta-plans des compétences (Skills) enregistrées."

    def get_available_goals(self) -> List[str]:
        return [
            "list_skills",
            "get_skill_manifest",
            "get_skill_parameters",
            "get_skill_meta_plan",
            "search_skills_by_keyword"
        ]

    def get_non_cacheable_goals(self) -> List[str]:
        return ["search_skills_by_keyword"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_skills",
                "description": _("Retourne la liste de tous les Skills disponibles avec leur statut et score de confiance."),
                "parameters": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "get_skill_manifest",
                "description": _("Retourne le manifeste complet d'un Skill (description, environnement, checkpoints). Paramètre : 'skill_id'."),
                "parameters": {
                    "type": "object",
                    "properties": {"skill_id": {"type": "string"}},
                    "required": ["skill_id"]
                }
            },
            {
                "name": "get_skill_parameters",
                "description": _("Retourne le schéma des paramètres d'entrée attendus par le Skill. Paramètre : 'skill_id'."),
                "parameters": {
                    "type": "object",
                    "properties": {"skill_id": {"type": "string"}},
                    "required": ["skill_id"]
                }
            },
            {
                "name": "get_skill_meta_plan",
                "description": _("Retourne la séquence des étapes techniques du méta-plan du Skill. Paramètre : 'skill_id'."),
                "parameters": {
                    "type": "object",
                    "properties": {"skill_id": {"type": "string"}},
                    "required": ["skill_id"]
                }
            },
            {
                "name": "search_skills_by_keyword",
                "description": _("Recherche des Skills par mot-clé dans leur nom ou description. Paramètre : 'keyword'."),
                "parameters": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                    "required": ["keyword"]
                }
            }
        ]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if tool_name == "list_skills":
                skills = self.skill_registry.list_all_skills()
                return {"success": True, "skills": skills, "count": len(skills)}

            if tool_name == "search_skills_by_keyword":
                kw = (args.get("keyword") or "").lower()
                all_skills = self.skill_registry.list_all_skills()
                matched = [
                    s for s in all_skills
                    if kw in s.get("id", "").lower() or kw in s.get("name", "").lower() or kw in s.get("description", "").lower()
                ]
                return {"success": True, "keyword": kw, "count": len(matched), "results": matched}

            skill_id = args.get("skill_id") or args.get("target")
            if not skill_id:
                return {"success": False, "error": "Paramètre 'skill_id' requis."}

            manifest, version = self.skill_registry.get_active_skill(skill_id)
            if not manifest:
                return {"success": False, "error": f"Skill '{skill_id}' introuvable."}

            if tool_name == "get_skill_manifest":
                return {
                    "success": True,
                    "skill_id": skill_id,
                    "name": manifest.name,
                    "namespace": manifest.namespace,
                    "description": manifest.description,
                    "status": version.state.value if version else "UNKNOWN",
                    "version": version.version if version else 1,
                    "risk_level": manifest.risk_level,
                    "preconditions": manifest.preconditions,
                    "postconditions": manifest.postconditions,
                    "environment_requirements": getattr(manifest.environment, "requirements", manifest.environment if isinstance(manifest.environment, dict) else {})
                }
            elif tool_name == "get_skill_parameters":
                return {
                    "success": True,
                    "skill_id": skill_id,
                    "parameters_schema": manifest.parameters_schema
                }
            elif tool_name == "get_skill_meta_plan":
                flow_payload = self.skill_registry.get_flow_payload(skill_id, version.version if version else None)
                return {
                    "success": True,
                    "skill_id": skill_id,
                    "version": version.version if version else 1,
                    "meta_plan": flow_payload.get("meta_plan", []) if flow_payload else []
                }
            else:
                return {"success": False, "error": f"Outil '{tool_name}' non reconnu par SkillsExplorer."}
        except Exception as e:
            Logger.error(f"[SkillsExplorer] Erreur lors de l'exécution de {tool_name}: {e}")
            return {"success": False, "error": str(e)}

    def validate_target(self, target: str, provider: Optional['DataProvider'] = None) -> bool:
        if provider:
            return target in provider.get_targets()
        try:
            return self.skill_registry.get_skill(target) is not None
        except Exception:
            return True

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        if not targets or not technical_goals:
            return f"{self._data_type}://unknown"
        if len(targets) == 1:
            return f"{self._data_type}://{targets[0]}/{technical_goals[0]}"
        targets_str = "_".join(targets)
        goals_str = "_".join(technical_goals)
        return f"{self._data_type}://multi/{targets_str}/{goals_str}"

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
            # Plan direct déterministe par défaut si aucun LLM dédié n'est passé
            tgt = target or (targets[0] if targets else "all")
            tg = technical_goal or (technical_goals[0] if technical_goals else "list_skills")
            
            tool_name = "list_skills"
            args = {}
            if tg == "get_skill_manifest":
                tool_name = "get_skill_manifest"
                args = {"skill_id": tgt}
            elif tg == "get_skill_parameters":
                tool_name = "get_skill_parameters"
                args = {"skill_id": tgt}
            elif tg == "get_skill_meta_plan":
                tool_name = "get_skill_meta_plan"
                args = {"skill_id": tgt}
            elif tg == "search_skills_by_keyword":
                tool_name = "search_skills_by_keyword"
                args = {"keyword": tgt}

            step = DiscoveryStep(
                id="step_0",
                type=StepType.TOOL,
                description=f"Action déterministe {tool_name} sur {tgt}",
                tool_name=tool_name,
                tool_args=args,
                expected_result="true"
            )
            return DiscoveryPlan(
                goal=goal,
                steps=[step],
                data_type=self._data_type,
                targets=[tgt],
                technical_goals=[tg],
                signature=self.create_signature([tgt], [tg])
            )

        if targets is None and target is not None:
            targets = [target]
        if technical_goals is None and technical_goal is not None:
            technical_goals = [technical_goal]

        if not targets or not technical_goals:
            raise ValueError(_("Au moins une cible et un goal technique doivent être spécifiés."))

        signature = self.create_signature(targets, technical_goals)

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

        with self.runtime_state.execution_context.scope(discovery_signature=signature):
            try:
                result: ExplorerPlanOutput = await effective_llm.generate_structured(
                    prompt=prompt,
                    schema=ExplorerPlanOutput,
                    tag="explorer_plan_generation"
                )
                steps = result.steps
            except Exception as e:
                raise ValueError(_("Échec de la génération du plan par le LLM de l'Explorer : {error}").format(error=e))

        discovery_steps = []
        tool_names = [t["name"] for t in tools_desc]
        for idx, step in enumerate(steps):
            if step.type == "tool":
                if not step.tool_name or step.tool_name not in tool_names:
                    continue
                try:
                    tool_args = json.loads(step.tool_args_json) if step.tool_args_json else {}
                except json.JSONDecodeError:
                    tool_args = {}

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
                discovery_steps.append(
                    DiscoveryStep(
                        id=f"step_{idx}",
                        type=StepType.SEMANTIC,
                        description=step.description,
                        question=step.question or goal
                    )
                )

        if not discovery_steps:
            tgt = targets[0] if targets else "all"
            tg = technical_goals[0] if technical_goals else "list_skills"
            tool_name = "list_skills"
            args = {}
            if tg in ("get_skill_manifest", "get_skill_parameters", "get_skill_meta_plan"):
                tool_name = tg
                args = {"skill_id": tgt}
            elif tg == "search_skills_by_keyword":
                tool_name = "search_skills_by_keyword"
                args = {"keyword": tgt}
            discovery_steps.append(
                DiscoveryStep(
                    id="step_0",
                    type=StepType.TOOL,
                    description=f"Action déterministe {tool_name} sur {tgt}",
                    tool_name=tool_name,
                    tool_args=args,
                    expected_result="true"
                )
            )

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

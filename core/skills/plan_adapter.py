# -*- coding: utf-8 -*-
"""
core/skills/plan_adapter.py
Adaptateur agnostique : Méta-Plan de Skill <---> Plan standard ManAgent.
Permet de convertir n'importe quel flux de Skill (JSON, dict, graphe ou liste de nœuds)
en un objet Plan standardisé composé de PlanStep, exécutable directement par l'Executor unifié.
"""

import json
from typing import Any, Dict, List, Optional
from core.plan_models import Plan, PlanStep, StepType
from utils.logger import Logger


def meta_plan_to_plan(
    meta_plan_data: Any,
    goal: str = "Skill Execution",
    step_id_prefix: Optional[str] = None
) -> Plan:
    """
    Convertit la structure méta-plan d'un skill en un Plan standardisé ManAgent.

    :param meta_plan_data: Dictionnaire avec clé 'meta_plan', liste de nœuds, ou string JSON
    :param goal: Objectif textuel du plan
    :param step_id_prefix: Préfixe optionnel pour identifier clairement les étapes du sous-plan
    :return: Instance Plan contenant les PlanStep prêts pour l'Executor
    """
    if isinstance(meta_plan_data, str):
        try:
            meta_plan_data = json.loads(meta_plan_data)
        except Exception as e:
            Logger.warning(f"[PlanAdapter] Échec du parsing JSON du flux : {e}")
            meta_plan_data = []

    # Extraction sécurisée de la liste des étapes
    if isinstance(meta_plan_data, dict):
        # Supporte format { "meta_plan": [...] }, { "steps": [...] }, ou { "graph": { "nodes": [...] } }
        if "meta_plan" in meta_plan_data and isinstance(meta_plan_data["meta_plan"], list):
            steps_raw = meta_plan_data["meta_plan"]
        elif "steps" in meta_plan_data and isinstance(meta_plan_data["steps"], list):
            steps_raw = meta_plan_data["steps"]
        elif "graph" in meta_plan_data and isinstance(meta_plan_data["graph"], dict):
            steps_raw = meta_plan_data["graph"].get("nodes", [])
        else:
            steps_raw = []
    elif isinstance(meta_plan_data, list):
        steps_raw = meta_plan_data
    else:
        steps_raw = []

    steps: List[PlanStep] = []

    for idx, node in enumerate(steps_raw, start=1):
        if not isinstance(node, dict):
            continue

        raw_id = node.get("step_id") or node.get("id") or node.get("node_id") or f"step_{idx}"
        if step_id_prefix:
            step_id = f"{step_id_prefix}_{raw_id}"
        else:
            step_id = raw_id

        tool_name = node.get("tool_name") or node.get("tool") or node.get("action")
        
        # Support agnostique des différentes dénominations d'arguments
        tool_args = node.get("tool_args")
        if tool_args is None:
            tool_args = node.get("arguments")
        if tool_args is None:
            tool_args = node.get("args")
        if tool_args is None:
            tool_args = node.get("parameters")
        if tool_args is None:
            tool_args = node.get("tool_args_json")
        if tool_args is None:
            tool_args = {}

        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {"action": tool_args}
        elif not isinstance(tool_args, dict):
            tool_args = {}

        # Si le nœud a une sous-action atomique spécifiée au niveau racine (ex: action="click")
        action_cand = node.get("action")
        if action_cand and isinstance(action_cand, str) and action_cand != tool_name:
            if "action" not in tool_args and " " not in action_cand and len(action_cand) < 50:
                tool_args["action"] = action_cand

        # Type d'étape
        raw_type = str(node.get("type", "")).strip().lower()
        if raw_type == "direct_answer" or (not tool_name and node.get("response_text")):
            step_type = StepType.DIRECT_ANSWER
        else:
            step_type = StepType.TOOL_CALL

        # Output variable name si spécifié
        out_var_name = node.get("output_variable_name") or node.get("output_variable")
        out_var_desc = node.get("output_variable_desc") or node.get("output_desc")

        step_obj = PlanStep(
            id=step_id,
            description=node.get("description") or node.get("title") or f"Action {tool_name or 'unitaire'}",
            type=step_type,
            tool_name=tool_name,
            tool_args_json=json.dumps(tool_args, ensure_ascii=False),
            expected_result=str(node.get("expected_result", "true")),
            execute_if=node.get("execute_if"),
            is_crucial=bool(node.get("is_crucial", False)),
            output_variable_name=out_var_name,
            output_variable_desc=out_var_desc,
            response_text=node.get("response_text")
        )

        steps.append(step_obj)

    return Plan(goal=goal, steps=steps)

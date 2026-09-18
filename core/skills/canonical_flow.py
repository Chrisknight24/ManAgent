# -*- coding: utf-8 -*-
"""
core/skills/canonical_flow.py
Format canonique abstrait et passerelle pour les méta-plans et flux d'exécution ManAgent.
ManAgent est 100% agnostique vis-à-vis des hôtes d'exécution et des formats tiers.
Le format canonique de ManAgent ('managent_flow') est un graphe abstrait de nœuds
représentant des invocations de primitives ('tool_name', 'tool_args', 'checkpoint_id').
"""

import json
import uuid
import time
from typing import List, Dict, Any, Tuple, Optional
from core.skills.models import SkillManifest, ExecutionEnvironment


class CanonicalFlowConverter:
    """
    Passerelle universelle et agnostique :
    ManAgent MetaPlan <---> Graphe de flux canonique ManAgent ('managent_flow')
    """

    CANONICAL_FORMAT = "managent_flow"
    CANONICAL_SCHEMA_VERSION = 2

    @classmethod
    def meta_plan_to_flow(
        cls,
        meta_plan: List[Dict[str, Any]],
        manifest: Optional[SkillManifest] = None,
        workflow_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Convertit un méta-plan ManAgent en structure de graphe canonique universelle.
        """
        w_id = f"flow_{uuid.uuid4().hex[:8]}"
        name = workflow_name or (manifest.name if manifest else "Exported Flow")
        desc = manifest.description if manifest else "Exported from ManAgent Skill Engine"

        nodes = []
        connections = []
        prev_node_id = None

        for idx, step in enumerate(meta_plan or []):
            node_id = step.get("step_id") or f"node_{idx + 1}_{uuid.uuid4().hex[:6]}"
            tool = step.get("tool_name") or step.get("action") or "generic_tool"
            args = step.get("arguments") or step.get("tool_args") or step.get("args") or {}
            checkpoint_id = step.get("checkpoint_id") or f"cp_{idx + 1}"

            flow_node = {
                "id": node_id,
                "tool_name": tool,
                "title": step.get("description") or f"Step {idx + 1}: {tool}",
                "checkpoint": checkpoint_id,
                "parameters": args,
                "timeout_ms": step.get("timeout_ms", 5000),
                "retry_count": step.get("retry_count", 0),
                "position": {
                    "x": 200,
                    "y": 100 + (idx * 100)
                }
            }
            nodes.append(flow_node)

            # Connexion séquentielle par défaut
            if prev_node_id:
                connections.append({
                    "from_node": prev_node_id,
                    "to_node": node_id
                })
            prev_node_id = node_id

        flow_document = {
            "version": "2.0.0",
            "format": cls.CANONICAL_FORMAT,
            "schema_version": cls.CANONICAL_SCHEMA_VERSION,
            "created_at": time.time(),
            "metadata": {
                "id": w_id,
                "name": name,
                "description": desc,
                "skill_id": manifest.skill_id if manifest else None,
                "risk_level": manifest.risk_level if manifest else "low",
                "environment": manifest.environment.to_dict() if manifest else {}
            },
            "graph": {
                "nodes": nodes,
                "connections": connections
            },
            "variables": manifest.parameters_schema if manifest else {}
        }
        return flow_document

    @classmethod
    def flow_to_meta_plan(cls, flow_dict: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Convertit un document de graphe canonique en méta-plan ManAgent et variables.
        """
        if not isinstance(flow_dict, dict):
            raise ValueError("Le document de flux doit être un dictionnaire JSON valide.")

        graph = flow_dict.get("graph", {})
        nodes = graph.get("nodes", [])

        meta_plan: List[Dict[str, Any]] = []

        for idx, n in enumerate(nodes):
            tool = n.get("tool_name") or n.get("type") or "generic_tool"
            # Si le type était préfixé (ex: Action.Click), extraire la primitive
            if "." in tool:
                tool = tool.split(".")[-1].lower()

            step = {
                "step_id": n.get("id") or f"step_{idx + 1}",
                "tool_name": tool,
                "description": n.get("title") or f"Action {tool}",
                "tool_args": n.get("parameters", {}),
                "checkpoint_id": n.get("checkpoint") or f"cp_{idx + 1}",
                "timeout_ms": n.get("timeout_ms", 5000)
            }
            meta_plan.append(step)

        meta_params = flow_dict.get("variables", {})
        return meta_plan, meta_params

    @classmethod
    def validate_flow_payload(cls, flow_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valide la conformité structurelle d'un graphe de flux canonique."""
        errors = []
        if not isinstance(flow_dict, dict):
            return False, ["Format racine non JSON."]

        if "graph" not in flow_dict:
            errors.append("Clé 'graph' absente du document.")
        else:
            g = flow_dict["graph"]
            if not isinstance(g.get("nodes"), list):
                errors.append("Clé 'graph.nodes' manquante ou invalide.")

        return (len(errors) == 0), errors

    # Méthodes d'alias de compatibilité
    meta_plan_to_flo = meta_plan_to_flow
    flo_to_meta_plan = flow_to_meta_plan
    validate_flo_payload = validate_flow_payload


# Alias rétrocompatible
FloConverterInterface = CanonicalFlowConverter

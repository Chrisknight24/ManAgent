#!/usr/bin/env python3
"""
build_observability_report.py (v8.27)
=====================================
- Rapport d'observabilité complet avec hiérarchie intégrale :
  Orchestrateur ➔ Solver (SignatureExtractor, FeasibilityDecision)
  ➔ Planner & Supervisor (PlanValidation) ➔ Executor (Steps, Outils, Sous-Solvers)
  ➔ Post-Exécution (MissionCompactor, Learner, Presentator).
- Panneau d'inspection latéral persistant et universel (Tchat & Missions).
- Détection et affichage explicite des rejets et échecs à chaque niveau :
  * Faisabilité rejetée (is_possible=false, manque de prérequis)
  * Plan rejeté par le Superviseur (approved=false, critiques, failles de sécurité)
  * Échecs d'outils / erreurs d'exécution / erreurs réseau
  * Rejet de convergence ou replanification
- Correction du routage Orchestrateur dans le tchat (différenciation exacte direct vs mission).
- Correction du clic Discovery (système de registre global par clé unique sans injection inline).
- Accordéons de tentatives et de sous-solvers synchronisés avec comptage logique (succès/ignorées/échecs).
- Discovery compact, repliable et scrollable à hauteur fixe.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from core.constants import RETRIEVAL_THRESHOLD, RETRIEVAL_TOP_K
except Exception:
    RETRIEVAL_THRESHOLD = 0.5
    RETRIEVAL_TOP_K = 3

# =====================================================
# CHARGEMENT DES DONNÉES
# =====================================================

def load_episodes(db_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM episodes ORDER BY created_at ASC")
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    conn.close()

    episodes = []
    for row in rows:
        try:
            tree = json.loads(row.get("execution_tree_json") or "{}")
        except Exception:
            tree = {}
        try:
            resolved = json.loads(row.get("resolved_data_json") or "{}")
        except Exception:
            resolved = {}
        presentator = None
        try:
            raw_pres = row.get("presentator_result_json")
            if raw_pres and raw_pres not in ("{}", "null", ""):
                presentator = json.loads(raw_pres)
        except Exception:
            presentator = None

        episodes.append({
            "mission_id": row.get("mission_id"),
            "session_id": row.get("session_id"),
            "goal": row.get("goal"),
            "environment": row.get("environment"),
            "status": row.get("status"),
            "execution_tree": tree,
            "resolved_data": resolved,
            "presentator_result": presentator,
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
            "analyzed_at": row.get("analyzed_at"),
            "refined_goal": None,
            "signatures": [],
            "_routing_call": None,
            "_solver_preparations": {},
            "_solver_retrieval": {},
            "_solver_planning": {},
            "_solver_validation": {},
            "_solver_post_execution": {},
            "_solver_discovery": {},
            "_presentator_discovery": [],
            "_discovery_sessions": [],
        })
    return episodes

def load_lessons(db_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM lessons WHERE is_active = 1 ORDER BY is_consolidated DESC, entity_type ASC, confidence DESC")
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    conn.close()

    lessons = []
    for row in rows:
        try:
            keywords = json.loads(row.get("keywords_json") or "[]")
        except Exception:
            keywords = []
        try:
            source_episodes = json.loads(row.get("source_episodes_json") or "[]")
        except Exception:
            source_episodes = []
        lessons.append({**row, "keywords": keywords, "source_episodes": source_episodes})
    return lessons

def load_events(events_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(events_path):
        return []
    events = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events

# =====================================================
# TAGS COGNITIFS & LLM
# =====================================================

PRESENTATOR_TAGS = {"generate_text", "Presentator_report", "Presentator_error", "Presentator_output", "Presentator"}
SIGNATURE_TAGS = {"SignatureExtractor"}
FEASIBILITY_TAGS = {"FeasibilityDecision"}
PLANNING_TAGS = {"Plan", "RerankedLessons"}
PLAN_VALIDATION_TAGS = {"PlanValidationDecision"}
COMPACTOR_TAGS = {"MissionCompactor"}
LEARNER_TAGS = {"ExtractedLesson", "Learner"}
POST_EXECUTION_TAGS = COMPACTOR_TAGS | LEARNER_TAGS
CONVERGENCE_TAGS = {"ConvergenceDecision"}
EXPLORER_PLAN_TAGS = {"explorer_plan_generation", "explorer_plan_generation_mission"}
DISCOVERY_LLM_TAGS = EXPLORER_PLAN_TAGS | {"analyze_registry", "analyze_execution_tree", "discovery_semantic"}

def _parse_ts(ts) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

def build_solver_to_mission_map(episodes):
    solver_to_mission = {}
    for ep in episodes:
        mission_id = ep.get("mission_id")
        if not mission_id:
            continue
        tree = ep.get("execution_tree")
        if not tree:
            continue
        def traverse(node):
            if not node:
                return
            sid = node.get("solver_id")
            if sid:
                solver_to_mission[sid] = mission_id
                clean_sid = sid.replace("solver_", "")
                solver_to_mission[clean_sid] = mission_id
            for attempt in node.get("attempts", []):
                for step_node in attempt.get("nodes", []):
                    child = step_node.get("child_execution_tree")
                    if child:
                        traverse(child)
        traverse(tree)
    return solver_to_mission

def _collect_attempts(tree, attempt_index, all_attempts):
    if not tree:
        return
    stack = [tree]
    while stack:
        current = stack.pop()
        solver_id = current.get("solver_id")
        for attempt in current.get("attempts", []):
            attempt_num = attempt.get("attempt_number")
            if solver_id is not None and attempt_num is not None:
                attempt_index[(solver_id, attempt_num)] = attempt
                attempt_index[(solver_id.replace("solver_", ""), attempt_num)] = attempt
            start = attempt.get("started_at")
            end = attempt.get("ended_at")
            all_attempts.append((attempt, start, end))
            for node in attempt.get("nodes", []):
                child_tree = node.get("child_execution_tree")
                if child_tree:
                    stack.append(child_tree)

# =====================================================
# RATTACHEMENT DES ÉVÉNEMENTS DISCOVERY
# =====================================================

def build_discovery_data(events: List[Dict], llm_calls: List[Dict]) -> Dict[str, Any]:
    discovery_events = [e for e in events if e.get("event", "").startswith("discovery.")]
    sessions_by_run = {}

    llm_calls_by_run = {}
    for call in llm_calls:
        tag = call.get("tag", "")
        if tag in DISCOVERY_LLM_TAGS:
            key = call.get("run_id") or call.get("discovery_run_id") or call.get("signature") or call.get("session_id")
            if key:
                llm_calls_by_run.setdefault(key, []).append(call)

    for ev in discovery_events:
        signature = ev.get("session_id")
        run_id = ev.get("run_id")
        is_legacy = run_id is None
        key = run_id or signature
        if not key:
            continue

        if key not in sessions_by_run:
            entity_id = ev.get("entity_id")
            entity_name = ev.get("entity_name")
            entity_role = ev.get("entity_role")
            if not entity_name or entity_name == "unknown":
                short_id = entity_id[:8] if entity_id and len(entity_id) >= 8 else (entity_id or "?")
                entity_name = short_id
            if not entity_role or entity_role == "unknown":
                entity_role = "?"

            caller = ev.get("caller") or ev.get("component") or ev.get("source")
            if not caller:
                if ev.get("step_id"):
                    caller = "step"
                elif ev.get("solver_id"):
                    caller = "solver"
                elif ev.get("turn_id") and not ev.get("mission_id"):
                    caller = "orchestrator"
                else:
                    caller = "mission"

            sessions_by_run[key] = {
                "run_id": run_id or key,
                "signature": signature,
                "legacy": is_legacy,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "entity_role": entity_role,
                "caller": caller,
                "solver_id": ev.get("solver_id"),
                "step_id": ev.get("step_id"),
                "mission_id": ev.get("mission_id"),
                "turn_id": ev.get("turn_id"),
                "goal": None,
                "data_type": None,
                "targets": [],
                "technical_goals": [],
                "exit_policy": None,
                "summary": None,
                "steps": [],
                "cache_hit": False,
                "explorer_plan_calls": llm_calls_by_run.get(run_id) or llm_calls_by_run.get(signature) or [],
                "start_time": None,
                "end_time": None,
            }
        session = sessions_by_run[key]
        if session.get("turn_id") is None and ev.get("turn_id"):
            session["turn_id"] = ev.get("turn_id")
        if session.get("mission_id") is None and ev.get("mission_id"):
            session["mission_id"] = ev.get("mission_id")
        if session.get("solver_id") is None and ev.get("solver_id"):
            session["solver_id"] = ev.get("solver_id")
        if session.get("step_id") is None and ev.get("step_id"):
            session["step_id"] = ev.get("step_id")
        if ev.get("caller") and not session.get("caller"):
            session["caller"] = ev.get("caller")

        if ev.get("event") == "discovery.session_start":
            session["goal"] = ev.get("goal")
            session["data_type"] = ev.get("data_type")
            session["targets"] = ev.get("targets", [])
            session["technical_goals"] = ev.get("technical_goals", [])
            session["cache_hit"] = ev.get("cache_hit", False)
            session["start_time"] = ev.get("ts")
        elif ev.get("event") == "discovery.session_end":
            session["exit_policy"] = ev.get("exit_policy")
            session["summary"] = ev.get("summary")
            session["end_time"] = ev.get("ts")
        elif ev.get("event") == "discovery.step":
            step = {
                "step_id": ev.get("step_id"),
                "step_type": ev.get("step_type"),
                "description": ev.get("description"),
                "tool_name": ev.get("tool_name"),
                "question": ev.get("question"),
                "result": ev.get("result", {}),
                "timestamp": ev.get("ts"),
            }
            session["steps"].append(step)
        elif ev.get("event") == "discovery.cache_hit":
            session["cache_hit"] = True

    sessions_by_run = {
        key: s for key, s in sessions_by_run.items()
        if s.get("goal") or s.get("data_type") or s.get("steps")
    }

    by_mission = {}
    by_turn = {}
    by_solver = {}
    by_step = {}

    for key, session in sessions_by_run.items():
        mid = session.get("mission_id")
        if mid:
            by_mission.setdefault(mid, []).append(session)
        tid = session.get("turn_id")
        if tid:
            by_turn.setdefault(tid, []).append(session)
        sid = session.get("solver_id")
        if sid:
            by_solver.setdefault(sid, []).append(session)
            by_solver.setdefault(sid.replace("solver_", ""), []).append(session)
        stp = session.get("step_id")
        if stp:
            by_step.setdefault(stp, []).append(session)

    unattached_sessions = [
        s for s in sessions_by_run.values()
        if not s.get("mission_id") and not s.get("turn_id")
    ]

    return {
        "by_mission": by_mission,
        "by_turn": by_turn,
        "by_solver": by_solver,
        "by_step": by_step,
        "by_run": sessions_by_run,
        "unattached_sessions": unattached_sessions,
    }

def attach_discovery_to_episodes(episodes, discovery_data):
    by_mission = discovery_data.get("by_mission", {})
    by_solver = discovery_data.get("by_solver", {})
    by_step = discovery_data.get("by_step", {})

    for ep in episodes:
        mid = ep.get("mission_id")
        m_sessions = by_mission.get(mid, [])
        ep["_discovery_sessions"] = m_sessions

        # Presentator Discovery
        ep["_presentator_discovery"] = [s for s in m_sessions if s.get("caller") == "presentator"]

        # Solver and Step level Discovery
        tree = ep.get("execution_tree")
        if tree:
            def traverse_discovery(node):
                if not node:
                    return
                sid = node.get("solver_id")
                if sid:
                    solver_disc = by_solver.get(sid, [])
                    if solver_disc:
                        ep.setdefault("_solver_discovery", {})[sid] = solver_disc

                for att in node.get("attempts", []):
                    for stp_node in att.get("nodes", []):
                        stp_id = stp_node.get("step_id")
                        if stp_id and stp_id in by_step:
                            stp_node["_discovery_sessions"] = by_step[stp_id]
                        child = stp_node.get("child_execution_tree")
                        if child:
                            traverse_discovery(child)
            traverse_discovery(tree)

# =====================================================
# RATTACHEMENT DE TOUTES LES ENTITÉS LLM & COGNITIVES
# =====================================================

def attach_llm_calls_by_mission(episodes, llm_calls, events):
    ep_index = {ep["mission_id"]: ep for ep in episodes if ep.get("mission_id")}
    solver_to_mission = build_solver_to_mission_map(episodes)

    all_attempts_global = []
    attempt_index_global = {}
    for ep in episodes:
        tree = ep.get("execution_tree") or {}
        _collect_attempts(tree, attempt_index_global, all_attempts_global)

    # Rattachement des événements logs du tool_manager
    tm_events = [e for e in events if str(e.get("event", "")).startswith("tools_manager.")]
    for ev in tm_events:
        step_id = ev.get("step_id")
        solver_id = ev.get("solver_id")
        attempt_num = ev.get("attempt_number")
        if step_id and solver_id is not None and attempt_num is not None:
            attempt = attempt_index_global.get((solver_id, attempt_num))
            if attempt:
                for node in attempt.get("nodes", []):
                    if node.get("step_id") == step_id:
                        node.setdefault("_tools_manager_events", []).append(ev)
                        break

    for call in llm_calls:
        tag = call.get("tag")
        if tag in DISCOVERY_LLM_TAGS:
            continue

        solver_id = call.get("solver_id")
        attempt_num = call.get("attempt_number")
        mid = call.get("mission_id")

        target_mid = mid
        if not target_mid or target_mid not in ep_index:
            if solver_id and solver_id in solver_to_mission:
                target_mid = solver_to_mission[solver_id]
            elif mid and mid in solver_to_mission:
                target_mid = solver_to_mission[mid]

        ep = ep_index.get(target_mid) if target_mid else None

        # 1. SignatureExtractor
        if tag in SIGNATURE_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_signatures", {}).setdefault(solver_id, []).append(call)
            elif ep:
                ep.setdefault("_solver_signatures", {}).setdefault("root_solver", []).append(call)
            continue

        # 2. FeasibilityDecision
        if tag in FEASIBILITY_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_feasibility", {}).setdefault(solver_id, []).append(call)
            if solver_id is not None and attempt_num is not None:
                attempt = attempt_index_global.get((solver_id, attempt_num))
                if attempt:
                    attempt.setdefault("_feasibility_calls", []).append(call)
            elif ep and not solver_id:
                ep.setdefault("_solver_feasibility", {}).setdefault("root_solver", []).append(call)
            continue

        # 3. MissionCompactor (Préparation / Contexte épisodique)
        if tag in COMPACTOR_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_compactor", {}).setdefault(solver_id, []).append(call)
            elif ep:
                ep.setdefault("_solver_compactor", {}).setdefault("root_solver", []).append(call)
            continue

        # 4. Planification (Planner, RerankedLessons)
        if tag in PLANNING_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_planning", {}).setdefault(solver_id, []).append(call)
            if solver_id is not None and attempt_num is not None:
                attempt = attempt_index_global.get((solver_id, attempt_num))
                if attempt:
                    attempt.setdefault("_planning_calls", []).append(call)
            elif ep:
                ep.setdefault("_solver_planning", {}).setdefault("root_solver", []).append(call)
            continue

        # 5. Superviseur / Validation de Plan (PlanValidationDecision)
        if tag in PLAN_VALIDATION_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_validation", {}).setdefault(solver_id, []).append(call)
            if solver_id is not None and attempt_num is not None:
                attempt = attempt_index_global.get((solver_id, attempt_num))
                if attempt:
                    attempt.setdefault("_validation_calls", []).append(call)
            elif ep:
                ep.setdefault("_solver_validation", {}).setdefault("root_solver", []).append(call)
            continue

        # 6. Learner / Leçons post-exécution
        if tag in LEARNER_TAGS:
            if ep and solver_id:
                ep.setdefault("_solver_learner", {}).setdefault(solver_id, []).append(call)
            elif ep:
                ep.setdefault("_solver_learner", {}).setdefault("root_solver", []).append(call)
            continue

        # 7. Tools Manager LLM Decision
        if tag == "tools_manager_decision":
            step_id = call.get("step_id")
            if step_id and solver_id is not None and attempt_num is not None:
                attempt = attempt_index_global.get((solver_id, attempt_num))
                if attempt:
                    for node in attempt.get("nodes", []):
                        if node.get("step_id") == step_id:
                            node.setdefault("_tools_manager_llm_calls", []).append(call)
                            break
            continue

        # 8. Convergence
        if tag in CONVERGENCE_TAGS:
            if solver_id is not None and attempt_num is not None:
                attempt = attempt_index_global.get((solver_id, attempt_num))
                if attempt:
                    attempt.setdefault("_convergence_calls", []).append(call)
                    continue
            call_ts = _parse_ts(call.get("ts"))
            if call_ts is not None:
                matched = None
                for attempt, start, end in all_attempts_global:
                    if start is None:
                        continue
                    if call_ts < start:
                        continue
                    if end is not None and call_ts > end:
                        continue
                    matched = attempt
                    break
                if matched:
                    matched.setdefault("_convergence_calls", []).append(call)
                    continue
            continue

        # 9. Presentator
        if tag in PRESENTATOR_TAGS:
            if ep:
                ep.setdefault("_presentator_calls", []).append(call)
            continue

        # 10. Steps & fallback
        if solver_id is not None and attempt_num is not None:
            attempt = attempt_index_global.get((solver_id, attempt_num))
            if attempt:
                step_id = call.get("step_id")
                if step_id:
                    for node in attempt.get("nodes", []):
                        if node.get("step_id") == step_id:
                            node.setdefault("_node_calls", []).append(call)
                            break
                else:
                    attempt.setdefault("_other_calls", []).append(call)
                continue

        if ep:
            ep.setdefault("_other_calls", []).append(call)

def attach_routing_calls_to_turns(session_turns, llm_calls):
    turns_by_session = {}
    for turn in session_turns:
        sid = turn.get("session_id")
        if sid:
            turns_by_session.setdefault(sid, []).append(turn)

    for sid in turns_by_session:
        turns_by_session[sid].sort(key=lambda t: _parse_ts(t.get("ts")) or 0)

    for call in llm_calls:
        if call.get("tag") != "OrchestratorDecision":
            continue
        sid = call.get("session_id")
        if not sid or sid not in turns_by_session:
            continue
        call_ts = _parse_ts(call.get("ts"))
        if call_ts is None:
            continue

        best = None
        best_diff = float('inf')
        for turn in turns_by_session[sid]:
            turn_ts = _parse_ts(turn.get("ts"))
            if turn_ts is not None:
                diff = abs(turn_ts - call_ts)
                if diff < best_diff:
                    best_diff = diff
                    best = turn

        if best:
            best["_routing_call"] = call

def parse_signature_string(s):
    if not isinstance(s, str):
        return s
    pattern = r"action=['\"]?([^'\"]+)['\"]?\s+object=['\"]?([^'\"]+)['\"]?(?:\s+desired_state=['\"]?([^'\"]+)['\"]?)?"
    match = re.search(pattern, s)
    if match:
        action = match.group(1)
        obj = match.group(2)
        desired = match.group(3) if match.group(3) is not None else None
        if desired == "None":
            desired = None
        return {"action": action, "object": obj, "desired_state": desired}
    return {"action": "?", "object": "?", "desired_state": None}

def build_data(
    db_path: str,
    events_path: str,
    target_session_id: Optional[str] = None,
    target_mission_id: Optional[str] = None,
    max_sessions: int = 20,
    session_only: bool = False,
) -> Dict[str, Any]:
    episodes = load_episodes(db_path)
    lessons = load_lessons(db_path)
    events = load_events(events_path)

    session_turns = [e for e in events if e.get("event") == "session_turn"]
    llm_calls = [e for e in events if e.get("event") == "llm_call"]
    retrieval_events = [e for e in events if e.get("event") in ("retriever_results", "retriever_query", "retriever_search_completed")]

    # Filtrage strict si session_only demandé
    if session_only and target_session_id:
        session_turns = [t for t in session_turns if t.get("session_id") == target_session_id]
        session_mids = {t.get("mission_id") for t in session_turns if t.get("mission_id")}
        if target_mission_id:
            session_mids.add(target_mission_id)
        if session_mids:
            episodes = [ep for ep in episodes if ep.get("mission_id") in session_mids]

    attach_llm_calls_by_mission(episodes, llm_calls, events)

    # Retrieval indexation par mission et par solver
    ep_index = {ep["mission_id"]: ep for ep in episodes if ep.get("mission_id")}
    solver_to_mission = build_solver_to_mission_map(episodes)

    for ev in retrieval_events:
        q_mid = ev.get("query_mission_id") or ev.get("mission_id")
        solver_id = ev.get("solver_id") or ev.get("query_mission_id") or ev.get("mission_id")
        
        target_mid = q_mid
        if not target_mid or target_mid not in ep_index:
            if solver_id and solver_id in solver_to_mission:
                target_mid = solver_to_mission[solver_id]
            elif solver_id and solver_id.replace("solver_", "") in solver_to_mission:
                target_mid = solver_to_mission[solver_id.replace("solver_", "")]

        if target_mid and target_mid in ep_index:
            ep = ep_index[target_mid]
            ep.setdefault("_solver_retrieval", {})
            if solver_id:
                ep["_solver_retrieval"].setdefault(solver_id, []).append(ev)
                ep["_solver_retrieval"].setdefault(solver_id.replace("solver_", ""), []).append(ev)
            else:
                ep["_solver_retrieval"].setdefault(target_mid, []).append(ev)
                ep["_solver_retrieval"].setdefault("root_solver", []).append(ev)

    # Solver Registries
    solver_registries = {}
    for ev in events:
        if ev.get("event") == "solver_registry":
            solver_id = ev.get("solver_id")
            if solver_id:
                solver_registries[solver_id] = ev.get("registry", {})
    for ep in episodes:
        ep["_registries"] = solver_registries

    discovery_data = build_discovery_data(events, llm_calls)
    attach_discovery_to_episodes(episodes, discovery_data)

    discovery_by_turn = discovery_data.get("by_turn", {})
    discovery_by_mission = discovery_data.get("by_mission", {})

    for turn in session_turns:
        turn_id = turn.get("turn_id")
        sessions_disc = discovery_by_turn.get(turn_id, []) if turn_id else []
        if not sessions_disc and turn.get("mission_id"):
            sessions_disc = [s for s in discovery_by_mission.get(turn.get("mission_id"), []) if s.get("caller") == "orchestrator"]
        turn["_discovery_sessions"] = sessions_disc

    for turn in session_turns:
        mission_id = turn.get("mission_id")
        if mission_id:
            ep = next((e for e in episodes if e.get("mission_id") == mission_id), None)
            if ep:
                if turn.get("refined_goal"):
                    ep["refined_goal"] = turn.get("refined_goal")
                if turn.get("signatures"):
                    raw_sigs = turn.get("signatures")
                    parsed_sigs = []
                    for s in raw_sigs:
                        if isinstance(s, dict):
                            parsed_sigs.append(s)
                        else:
                            parsed_sigs.append(parse_signature_string(s))
                    ep["signatures"] = parsed_sigs

    attach_routing_calls_to_turns(session_turns, llm_calls)

    # Attach routing call to episodes as well
    for turn in session_turns:
        mid = turn.get("mission_id")
        if mid and turn.get("_routing_call"):
            ep = ep_index.get(mid)
            if ep and not ep.get("_routing_call"):
                ep["_routing_call"] = turn.get("_routing_call")

    sessions = {}
    for turn in session_turns:
        sessions.setdefault(turn.get("session_id", "?"), []).append(turn)
    for turns in sessions.values():
        turns.sort(key=lambda t: t.get("ts") or "", reverse=False)

    # Tri par récence (la session la plus récente en premier)
    sorted_session_list = sorted(
        [{"session_id": sid, "turns": turns} for sid, turns in sessions.items()],
        key=lambda s: (s["turns"][-1].get("ts") if s.get("turns") and s["turns"][-1].get("ts") else (s["turns"][0].get("ts") if s.get("turns") else "")),
        reverse=True
    )

    if target_session_id and not session_only:
        target_item = next((s for s in sorted_session_list if s["session_id"] == target_session_id), None)
        other_items = [s for s in sorted_session_list if s["session_id"] != target_session_id]
        if target_item:
            if max_sessions and max_sessions > 0:
                sorted_session_list = [target_item] + other_items[:max_sessions - 1]
            else:
                sorted_session_list = [target_item] + other_items
        elif max_sessions and max_sessions > 0:
            sorted_session_list = sorted_session_list[:max_sessions]
    elif max_sessions and max_sessions > 0:
        sorted_session_list = sorted_session_list[:max_sessions]

    return {
        "episodes": episodes,
        "lessons": lessons,
        "sessions": sorted_session_list,
        "discovery_registry": discovery_data.get("by_run", {}),
        "target_session_id": target_session_id,
        "target_mission_id": target_mission_id,
        "clock_offset_detected": 0,
    }

# =====================================================
# GABARIT HTML COMPLET & MODERNE
# =====================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Observabilité & Traces — ManAgent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-alt: #f1f5f9;
  --surface-hover: #f8fafc;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --text: #0f172a;
  --text-muted: #475569;
  --text-faint: #94a3b8;
  
  --primary: #2563eb;
  --primary-bg: #eff6ff;
  --primary-border: #bfdbfe;
  
  --success: #16a34a;
  --success-bg: #f0fdf4;
  --success-border: #bbf7d0;
  
  --failure: #dc2626;
  --failure-bg: #fef2f2;
  --failure-border: #fecaca;
  
  --warning: #d97706;
  --warning-bg: #fffbeb;
  --warning-border: #fde68a;
  
  --purple: #7c3aed;
  --purple-bg: #f5f3ff;
  --purple-border: #ddd6fe;

  --user-bubble-bg: #1e293b;
  --user-bubble-text: #f8fafc;

  --sans: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Consolas, monospace;
  
  --shadow-xs: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.07), 0 1px 2px -1px rgba(0, 0, 0, 0.07);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -2px rgba(0, 0, 0, 0.08);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -4px rgba(0, 0, 0, 0.08);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14.5px;
  line-height: 1.5;
  overflow: hidden;
}

::-webkit-scrollbar { width: 7px; height: 7px; }
::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }

.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

/* RESIZERS FOR DYNAMIC PANELS */
.resizer {
  width: 6px;
  height: 100vh;
  background: var(--border);
  cursor: col-resize;
  flex-shrink: 0;
  transition: background 0.15s ease;
  z-index: 30;
  user-select: none;
}
.resizer:hover, .resizer.dragging {
  background: var(--primary);
}

/* SIDEBAR */
.sidebar {
  width: 290px;
  min-width: 180px;
  max-width: 480px;
  flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  z-index: 10;
  transition: width 0.05s ease;
}
.sidebar.collapsed {
  display: none !important;
}
.sidebar__header {
  padding: 16px 18px;
  border-bottom: 1px solid var(--border);
  background: #ffffff;
}
.sidebar__brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.sidebar__logo-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 800;
  font-size: 15px;
}
.sidebar__title { font-size: 16px; font-weight: 800; letter-spacing: -0.02em; color: var(--text); }
.sidebar__subtitle { font-size: 11px; color: var(--text-faint); margin-top: 1px; font-family: var(--mono); }

.nav-tabs {
  display: flex;
  padding: 8px 12px;
  gap: 6px;
  background: var(--surface-alt);
  border-bottom: 1px solid var(--border);
}
.nav-tab {
  flex: 1;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 12.5px;
  font-weight: 700;
  text-align: center;
  cursor: pointer;
  border: 1px solid transparent;
  color: var(--text-muted);
  background: transparent;
  transition: all 0.15s ease;
}
.nav-tab:hover { color: var(--text); background: rgba(255,255,255,0.6); }
.nav-tab.active {
  background: var(--surface);
  color: var(--primary);
  border-color: var(--border);
  box-shadow: var(--shadow-xs);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.session-card {
  padding: 10px 12px;
  border-radius: 9px;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--border);
  transition: all 0.15s ease;
}
.session-card:hover {
  border-color: var(--primary-border);
  background: var(--surface-hover);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.session-card.active {
  border-color: var(--primary);
  background: var(--primary-bg);
  box-shadow: var(--shadow-xs);
}
.session-card__id {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}
.session-card.active .session-card__id { color: var(--primary); }
.session-card__info {
  font-size: 12.5px;
  font-weight: 700;
  margin-top: 3px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-card__meta {
  font-size: 10.5px;
  color: var(--text-faint);
  margin-top: 3px;
  font-family: var(--mono);
}

/* MAIN CONTENT WITH PERSISTENT SPLIT INSPECTOR */
.main-content {
  flex: 1;
  height: 100vh;
  overflow: hidden;
  display: flex;
  background: var(--bg);
  min-width: 0;
}

.workspace-area {
  flex: 1;
  height: 100%;
  overflow-y: auto;
  overflow-x: auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.workspace-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: #ffffff;
  border-bottom: 1px solid var(--border);
  gap: 12px;
  flex-shrink: 0;
  z-index: 5;
}
.tb-btn {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
  background: var(--surface-alt);
  border: 1px solid var(--border);
  padding: 5px 10px;
  border-radius: 7px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 0.12s ease;
  user-select: none;
}
.tb-btn:hover {
  background: #ffffff;
  color: var(--primary);
  border-color: var(--primary-border);
}
.tb-btn-close {
  background: transparent;
  border: none;
  font-size: 14px;
  font-weight: 800;
  color: var(--text-faint);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all 0.12s ease;
}
.tb-btn-close:hover {
  background: var(--failure-bg);
  color: var(--failure);
}

.view-container {
  display: none;
  width: 100%;
  min-width: 0;
}
.view-container.active { display: block; }

/* CHAT TIMELINE */
.chat-view {
  padding: 18px clamp(12px, 3vw, 28px) 60px;
  max-width: 1050px;
  margin: 0 auto;
  width: 100%;
  min-width: 0;
}

.chat-turn {
  margin-bottom: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-turn__user-row {
  display: flex;
  justify-content: flex-end;
  align-items: flex-end;
  gap: 8px;
  width: 100%;
}
.chat-bubble-user {
  background: var(--user-bubble-bg);
  color: var(--user-bubble-text);
  padding: 12px 18px;
  border-radius: 18px 18px 4px 18px;
  font-size: 14.5px;
  font-weight: 500;
  line-height: 1.55;
  max-width: 88%;
  box-shadow: var(--shadow-sm);
  overflow-wrap: anywhere;
  word-break: normal;
}
.chat-bubble-user__content {
  white-space: pre-wrap;
}
.chat-bubble-user__content.collapsed-text {
  max-height: 220px;
  overflow: hidden;
  position: relative;
}
.chat-bubble-user__content.collapsed-text::after {
  content: "";
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 70px;
  background: linear-gradient(to bottom, transparent, var(--user-bubble-bg));
  pointer-events: none;
}
.chat-bubble-user__toggle {
  margin-top: 8px;
  font-size: 11.5px;
  font-weight: 700;
  color: #93c5fd;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(255,255,255,0.12);
  padding: 4px 10px;
  border-radius: 6px;
  user-select: none;
  transition: background 0.12s ease;
}
.chat-bubble-user__toggle:hover {
  background: rgba(255,255,255,0.22);
  color: #ffffff;
}
.btn-copy-bubble {
  font-size: 10.5px;
  font-weight: 700;
  color: #cbd5e1;
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.15);
  padding: 2px 7px;
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.12s ease;
  user-select: none;
}
.btn-copy-bubble:hover {
  background: rgba(255,255,255,0.25);
  color: #ffffff;
}
.chat-turn__time {
  font-size: 11px;
  color: var(--text-faint);
  font-family: var(--mono);
  margin-bottom: 2px;
}

.chat-bubble-bot {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 18px 18px 18px 4px;
  padding: 16px 20px;
  max-width: 90%;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-wrap: anywhere;
  word-break: normal;
}
.bot-header-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 800;
  font-family: var(--mono);
  color: var(--primary);
  text-transform: uppercase;
}

.mission-banner {
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 14px;
  padding: 16px 20px;
  max-width: 92%;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: all 0.15s ease;
  position: relative;
  overflow: hidden;
}
.mission-banner:hover {
  border-color: var(--primary);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}
.mission-banner::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  background: linear-gradient(to bottom, #2563eb, #7c3aed);
}
.mission-banner__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}
.mission-banner__title {
  font-size: 15.5px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.4;
}
.mission-banner__footer {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--primary);
  font-weight: 700;
  flex-wrap: wrap;
  gap: 8px;
}

/* BADGES */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  font-family: var(--mono);
  white-space: nowrap;
  border: none;
  background: none;
}
.badge--success { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
.badge--failed { background: var(--failure-bg); color: var(--failure); border: 1px solid var(--failure-border); }
.badge--skipped { background: var(--warning-bg); color: var(--warning); border: 1px solid var(--warning-border); }
.badge--pending { background: var(--surface-alt); color: var(--text-muted); border: 1px solid var(--border); }
.badge--primary { background: var(--primary-bg); color: var(--primary); border: 1px solid var(--primary-border); }
.badge--purple { background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple-border); }

/* INSPECTOR PANE (PERSISTANT & RESIZABLE) */
.inspector-pane {
  width: 380px;
  min-width: 250px;
  max-width: 650px;
  flex-shrink: 0;
  height: 100vh;
  background: var(--surface);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: -4px 0 16px rgba(0,0,0,0.03);
  transition: width 0.05s ease;
}
.inspector-pane.collapsed {
  display: none !important;
}
.inspector-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ffffff;
}
.inspector-title { font-size: 14.5px; font-weight: 800; color: var(--text); }
.inspector-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px;
}

/* CARDS & PIPELINE HIERARCHY */
.tree-node-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 12px 14px;
  margin-bottom: 10px;
  box-shadow: var(--shadow-xs);
  transition: all 0.12s ease;
  cursor: pointer;
  min-width: 0;
  word-break: break-word;
}
.tree-node-card:hover {
  border-color: var(--primary-border);
  box-shadow: var(--shadow-sm);
}
.tree-node-card.selected {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px var(--primary-border);
  background: #f8fbff;
}
.tree-node-card--solver { border-left: 4px solid var(--purple); }
.tree-node-card--prep { border-left: 4px solid #0284c7; }
.tree-node-card--plan { border-left: 4px solid #2563eb; }
.tree-node-card--validation { border-left: 4px solid #d97706; }
.tree-node-card--step { border-left: 4px solid #10b981; }
.tree-node-card--post { border-left: 4px solid #8b5cf6; }
.tree-node-card--discovery { border-left: 4px solid #06b6d4; }
.tree-node-card--rejected { border-left: 4px solid var(--failure); background: #fffcfc; }

/* ALERT & REJECTION BANNERS */
.rejection-banner {
  background: var(--failure-bg);
  border: 1px solid var(--failure-border);
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 8px;
  font-size: 12.5px;
  color: #991b1b;
}
.rejection-banner__title {
  font-weight: 800;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  margin-bottom: 4px;
}

/* ABSTRACT TASK / SUB-SOLVER ACCORDIONS */
.sub-solver-accordion {
  margin-left: 14px;
  margin-top: 8px;
  margin-bottom: 12px;
  border-left: 2px dashed var(--purple-border);
  padding-left: 12px;
}
.sub-solver-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #faf5ff;
  border: 1px solid var(--purple-border);
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  font-weight: 700;
  font-size: 12.5px;
  margin-bottom: 8px;
  transition: background 0.15s ease;
}
.sub-solver-header:hover { background: #f3e8ff; }

/* TENTATIVE ACCORDION */
.attempt-accordion {
  margin-left: 12px;
  margin-top: 8px;
  margin-bottom: 12px;
  border-left: 2px solid var(--border);
  padding-left: 12px;
}
.attempt-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--surface-alt);
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  font-weight: 700;
  font-size: 12.5px;
  margin-bottom: 8px;
  transition: background 0.15s ease;
}
.attempt-header:hover { background: #e2e8f0; }

/* DISCOVERY EMBEDDED CONTAINERS (SCROLLABLE & COMPACT) */
.discovery-container-scroll {
  max-height: 240px;
  overflow-y: auto;
  padding-right: 4px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.discovery-accordion-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  font-weight: 700;
  color: #0f766e;
  margin-bottom: 6px;
}
.discovery-accordion-header:hover { background: #ccfbf1; }

.discovery-chip-compact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #ffffff;
  border: 1px solid var(--border);
  border-left: 4px solid #06b6d4;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.12s ease;
}
.discovery-chip-compact:hover {
  border-color: #06b6d4;
  background: #f0fdfa;
  box-shadow: var(--shadow-xs);
}

.code-box {
  background: #0f172a;
  color: #f8fafc;
  font-family: var(--mono);
  font-size: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  line-height: 1.5;
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--primary);
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 16px;
  transition: all 0.12s ease;
}
.back-btn:hover { background: var(--surface-alt); border-color: var(--primary-border); }

/* TAB NAV IN INSPECTOR */
.inspector-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  background: var(--surface-alt);
}
.inspector-tab-btn {
  flex: 1;
  padding: 9px;
  font-size: 11.5px;
  font-weight: 700;
  text-align: center;
  color: var(--text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.inspector-tab-btn.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  background: var(--surface);
}

.lesson-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
  padding: 24px;
}
.lesson-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  box-shadow: var(--shadow-xs);
}
.lesson-box.polarity-prefer { border-top: 4px solid var(--success); }
.lesson-box.polarity-avoid { border-top: 4px solid var(--failure); }

</style>
</head>
<body>

<div class="app-layout">
  <!-- SIDEBAR -->
  <div class="sidebar" id="sidebar">
    <div class="sidebar__header">
      <div class="sidebar__brand">
        <div class="sidebar__logo-icon">M</div>
        <div>
          <div class="sidebar__title">ManAgent Trace</div>
          <div class="sidebar__subtitle" id="gen-timestamp">Observabilité Hiérarchique</div>
        </div>
      </div>
    </div>
    <div class="nav-tabs">
      <div class="nav-tab active" data-nav="sessions">Sessions</div>
      <div class="nav-tab" data-nav="lessons">Leçons & Mémoire</div>
    </div>
    <div class="session-list" id="session-list"></div>
  </div>

  <!-- RESIZER SIDEBAR -->
  <div class="resizer" id="resizer-sidebar" title="Glisser pour redimensionner le panneau latéral"></div>

  <!-- WORKSPACE AREA -->
  <div class="main-content">
    <div class="workspace-area" id="workspace-area">
      <!-- TOP TOOLBAR -->
      <div class="workspace-toolbar">
        <div style="display:flex; align-items:center; gap:10px;">
          <button class="tb-btn" id="btn-toggle-sidebar" onclick="toggleSidebar()" title="Masquer / Afficher les sessions">
            <span id="icon-sidebar-toggle">☰</span> Sessions
          </button>
          <span style="font-size:12px; color:var(--border-strong);">|</span>
          <span style="font-size:13px; font-weight:800; color:var(--text);" id="workspace-title">Fil d'Observabilité</span>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <button class="tb-btn" id="btn-toggle-inspector" onclick="toggleInspector()" title="Masquer / Afficher l'Inspecteur">
            🔍 Inspecteur de Traces <span class="badge badge--primary" id="tb-inspector-indicator" style="font-size:10px; padding:1px 5px;">380px</span>
          </button>
        </div>
      </div>

      <!-- VIEW SESSIONS -->
      <div class="view-container active" id="view-sessions">
        <div class="chat-view" id="chat-thread"></div>
      </div>

      <!-- VIEW MISSION DETAIL -->
      <div class="view-container" id="view-mission">
        <div style="padding:16px clamp(12px, 2.5vw, 28px) 80px; width: 100%; min-width: 0; box-sizing: border-box;" id="mission-tree-pane"></div>
      </div>

      <!-- VIEW LESSONS -->
      <div class="view-container" id="view-lessons">
        <div style="padding:24px 32px 0; max-width:1200px; margin:0 auto;">
          <h2 style="font-size:22px; font-weight:800; margin-bottom:16px;">Base de Connaissances & Leçons Extraites</h2>
          <div style="display:flex; gap:10px; margin-bottom:16px;" id="lessons-filter-bar"></div>
        </div>
        <div class="lesson-grid" id="lesson-grid-content"></div>
      </div>
    </div>

    <!-- RESIZER INSPECTOR -->
    <div class="resizer" id="resizer-inspector" title="Glisser pour redimensionner l'Inspecteur"></div>

    <!-- PERSISTENT INSPECTOR -->
    <div class="inspector-pane" id="inspector-pane">
      <div class="inspector-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <div class="inspector-title" id="inspector-title">Inspecteur de Traces</div>
          <span class="badge badge--primary" id="inspector-badge">Info</span>
        </div>
        <button class="tb-btn-close" onclick="toggleInspector(false)" title="Masquer l'Inspecteur">✕</button>
      </div>
      <div class="inspector-tabs" id="inspector-tabs" style="display:none;">
        <div class="inspector-tab-btn active" data-tab="overview">Synthèse</div>
        <div class="inspector-tab-btn" data-tab="prompt">Prompts & LLM</div>
        <div class="inspector-tab-btn" data-tab="raw">Données Brutes</div>
      </div>
      <div class="inspector-body" id="inspector-body">
        <div style="color:var(--text-faint); text-align:center; padding: 40px 10px;">
          Cliquez sur un message, une décision de routage Orchestrateur, un Solver, une sous-tâche, un Step ou une exploration Discovery pour inspecter l'intégralité des prompts et données.
        </div>
      </div>
    </div>
  </div>
</div>

<script id="data-island" type="application/json">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById('data-island').textContent);

// Global Registry for Discovery Sessions (Key -> Session)
const DISCOVERY_REGISTRY = {};
if (DATA.discovery_registry) {
  Object.assign(DISCOVERY_REGISTRY, DATA.discovery_registry);
}
// Also index all embedded discovery sessions
DATA.sessions.forEach(s => {
  (s.turns || []).forEach(t => {
    (t._discovery_sessions || []).forEach(ds => {
      const key = ds.run_id || ds.signature;
      if (key) DISCOVERY_REGISTRY[key] = ds;
    });
  });
});
DATA.episodes.forEach(ep => {
  (ep._discovery_sessions || []).forEach(ds => {
    const key = ds.run_id || ds.signature;
    if (key) DISCOVERY_REGISTRY[key] = ds;
  });
  if (ep._solver_discovery) {
    Object.values(ep._solver_discovery).forEach(list => {
      list.forEach(ds => {
        const key = ds.run_id || ds.signature;
        if (key) DISCOVERY_REGISTRY[key] = ds;
      });
    });
  }
});

let currentNav = 'sessions';

// Extraction ciblée depuis l'URL ou DATA
const urlParams = new URLSearchParams(window.location.search);
const hashStr = window.location.hash || '';

let urlMission = urlParams.get('mission');
let urlSession = urlParams.get('session');

if (!urlMission && hashStr.startsWith('#mission/')) {
  urlMission = decodeURIComponent(hashStr.replace('#mission/', '')).trim();
}
if (!urlSession && hashStr.startsWith('#session/')) {
  urlSession = decodeURIComponent(hashStr.replace('#session/', '')).trim();
}

let initialTargetSession = DATA.target_session_id || urlSession || null;
let initialTargetMission = DATA.target_mission_id || urlMission || null;

// Si une mission cible est spécifiée mais pas de session, trouver la session qui contient cette mission
if (initialTargetMission && !initialTargetSession) {
  for (const s of DATA.sessions) {
    if (s.turns && s.turns.some(t => t.mission_id === initialTargetMission)) {
      initialTargetSession = s.session_id;
      break;
    }
  }
}

let currentSessionId = initialTargetSession || (DATA.sessions[0] ? DATA.sessions[0].session_id : null);
let currentMissionId = initialTargetMission || null;
let selectedInspectorData = null;
let activeInspectorTab = 'overview';


function inspectAttemptPlanningCall(missionId, solverId, attNum, idx) {
  const ep = findEpisode(missionId);
  const node = (ep.execution_tree?.attempts || []).find(a => a.attempt_number === attNum);
  const calls = node?._planning_calls || [];
  const call = calls[idx];
  if (!call) return;
  const resp = call?.response || {};
  const plan = resp.plan || resp.steps || (Array.isArray(resp) ? resp : []);

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Ingénierie de Plan (Tentative #${attNum})</div>
      <div style="font-size:14px; margin-top:4px;">Le Planner a traduit la stratégie en étapes formelles HTN pour <b>${esc(solverId)}</b>.</div>
    </div>`;

  if (Array.isArray(plan) && plan.length > 0) {
    overview += `<div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:8px;">Plan Proposé (${plan.length} étapes)</div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${plan.map((st, sIdx) => `
          <div style="background:var(--surface-alt); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:12.5px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-family:var(--mono); font-weight:800; color:var(--primary);">#${sIdx + 1} · ${esc(st.id || 'step')}</span>
              ${st.type ? `<span style="font-size:10px; padding:2px 6px; background:var(--surface); border:1px solid var(--border); border-radius:4px;">${esc(st.type)}</span>` : ''}
            </div>
            <div style="margin-top:6px; color:var(--text);">${esc(st.description || '')}</div>
          </div>
        `).join('')}
      </div>
    </div>`;
  }
  overview += `</div>`;
  
  updateInspector(`Planner - ${solverId}`, 'Planner', overview, call ? [call] : [], call);
}


function inspectAttemptConvergenceCall(missionId, solverId, attNum, idx) {
  const ep = findEpisode(missionId);
  const node = (ep.execution_tree?.attempts || []).find(a => a.attempt_number === attNum);
  const calls = node?._convergence_calls || [];
  const call = calls[idx];
  if (!call) return;
  const resp = call?.response || {};
  const isConv = resp.is_convergent;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Évaluation de Convergence (Tentative #${attNum})</div>
      <div style="font-size:14px; margin-top:4px;">Validation sémantique pour vérifier si l'exécution d'une étape a atteint l'objectif technique attendu.</div>
    </div>
      
    <div style="background:var(--surface-alt); padding:12px; border-radius:10px; border-left:4px solid ${isConv ? 'var(--success)' : 'var(--failure)'}; border:1px solid var(--border);">
      <div style="font-weight:800; font-size:13.5px;">Statut : ${isConv ? '✅ CONVERGENCE ATTEINTE' : '❌ CONVERGENCE ÉCHOUÉE'}</div>
      ${resp.reason ? `
        <div style="font-size:12.5px; margin-top:6px; color:${isConv ? 'var(--text-muted)' : 'var(--failure)'};">
          <b>Raisonnement :</b> ${esc(resp.reason)}
        </div>
      ` : ''}
    </div>
  </div>`;
  updateInspector(`Convergence - ${solverId}`, 'Convergence', overview, call ? [call] : [], call);
}
function inspectAttemptValidationCall(missionId, solverId, attNum, idx) {
  const ep = findEpisode(missionId);
  const node = (ep.execution_tree?.attempts || []).find(a => a.attempt_number === attNum);
  const calls = node?._validation_calls || [];
  const call = calls[idx];
  if (!call) return;
  const resp = call?.response || {};
  const isAppr = resp.is_conformant !== false && resp.approved !== false && resp.is_valid !== false;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Supervision & Juge (Tentative #${attNum})</div>
      <div style="font-size:14px; margin-top:4px;">Validation formelle du plan par le Superviseur avant toute exécution.</div>
    </div>
      
    <div style="background:var(--surface-alt); padding:12px; border-radius:10px; border-left:4px solid ${isAppr ? 'var(--success)' : 'var(--failure)'}; border:1px solid var(--border);">
      <div style="font-weight:800; font-size:13.5px;">Statut : ${isAppr ? '✅ PLAN VALIDÉ' : '❌ PLAN REJETÉ'}</div>
      ${resp.critique || resp.reason || resp.feedback ? `
        <div style="font-size:12.5px; margin-top:6px; color:${isAppr ? 'var(--text-muted)' : 'var(--failure)'};">
          <b>Critique :</b> ${esc(resp.critique || resp.reason || resp.feedback)}
        </div>
      ` : ''}
    </div>
  </div>`;
  updateInspector(`Validator - ${solverId}`, 'Validator', overview, call ? [call] : [], call);
}
// ==========================================
// FORMATTERS & HELPERS
// ==========================================
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
function fmtJson(obj) {
  if (obj === null || obj === undefined) return '';
  if (typeof obj === 'string') return obj;
  try { return JSON.stringify(obj, null, 2); } catch(e) { return String(obj); }
}
function formatTimestamp(ts) {
  if (!ts) return '—';
  let d;
  if (typeof ts === 'number' || (typeof ts === 'string' && !isNaN(parseFloat(ts)) && isFinite(ts))) {
    d = new Date(parseFloat(ts) * 1000);
  } else {
    d = new Date(ts);
  }
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleDateString('fr-FR', {month:'short', day:'numeric'}) + ' ' + d.toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}
function formatDuration(ms) {
  if (!ms || ms < 0) return '—';
  if (ms < 1000) return Math.round(ms) + 'ms';
  const sec = ms / 1000;
  if (sec < 60) return sec.toFixed(1) + 's';
  const min = Math.floor(sec / 60);
  return min + 'm ' + Math.round(sec % 60) + 's';
}
function statusBadge(status) {
  const cls = {success: 'success', failed: 'failed', skipped: 'skipped', pending: 'pending', cancelled: 'skipped', rejected: 'failed'}[status] || 'pending';
  const label = {success: 'Succès', failed: 'Échec', skipped: 'Ignoré', pending: 'En cours', cancelled: 'Annulé', rejected: 'Rejeté'}[status] || (status || '?');
  return `<span class="badge badge--${cls}">${esc(label)}</span>`;
}
function findEpisode(missionId) {
  return DATA.episodes.find(e => String(e.mission_id).trim() === String(missionId).trim());
}

// ==========================================
// NAVIGATION & VIEWS
// ==========================================
function setView(viewName) {
  document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.nav === viewName));
  
  if (viewName === 'sessions') {
    document.getElementById('view-sessions').classList.add('active');
  } else if (viewName === 'mission') {
    document.getElementById('view-mission').classList.add('active');
  } else if (viewName === 'lessons') {
    document.getElementById('view-lessons').classList.add('active');
    renderLessonsView('all');
  }
}

document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const nav = tab.dataset.nav;
    currentNav = nav;
    if (nav === 'sessions') {
      setView('sessions');
      if (currentSessionId) renderSessionThread(currentSessionId);
    } else if (nav === 'lessons') {
      setView('lessons');
    }
  });
});

let missionHistoryStack = [];

function selectSession(sid) {
  currentSessionId = sid;
  currentMissionId = null;
  missionHistoryStack = [];
  renderSidebar();
  setView('sessions');
  renderSessionThread(sid);
}

function openMission(missionId, pushToHistory = true) {
  if (pushToHistory && currentMissionId && currentMissionId !== missionId) {
    missionHistoryStack.push(currentMissionId);
  }
  currentMissionId = missionId;
  setView('mission');
  renderMissionDetail(missionId);
}

function backToSession() {
  currentMissionId = null;
  missionHistoryStack = [];
  setView('sessions');
  if (currentSessionId) renderSessionThread(currentSessionId);
}

function backToPreviousMission() {
  if (missionHistoryStack.length > 0) {
    const prev = missionHistoryStack.pop();
    openMission(prev, false);
  } else {
    backToSession();
  }
}

// ==========================================
// SIDEBAR SESSIONS LIST
// ==========================================
function renderSidebar() {
  const el = document.getElementById('session-list');
  const sortedSessions = DATA.sessions.slice().sort((a, b) => {
    const aTime = a.turns[0] ? new Date(a.turns[0].ts).getTime() : 0;
    const bTime = b.turns[0] ? new Date(b.turns[0].ts).getTime() : 0;
    return bTime - aTime;
  });
  
  el.innerHTML = sortedSessions.map(s => {
    const active = s.session_id === currentSessionId;
    const missionCount = s.turns.filter(t => t.mode === 'mission').length;
    const directCount = s.turns.length - missionCount;
    const firstTs = s.turns[0] ? formatTimestamp(s.turns[0].ts) : '';
    const lastMsg = s.turns[s.turns.length - 1]?.user_message || 'Session vide';
    const preview = lastMsg.length > 38 ? lastMsg.substring(0, 38) + '…' : lastMsg;

    return `<div class="session-card ${active ? 'active' : ''}" onclick="selectSession('${esc(s.session_id)}')">
      <div class="session-card__id">${esc(s.session_id).slice(0, 24)}</div>
      <div class="session-card__info">${esc(preview)}</div>
      <div class="session-card__meta">${firstTs} · ${s.turns.length} tour(s) (${missionCount} 🚀, ${directCount} 💬)</div>
    </div>`;
  }).join('') || '<div style="padding:20px;text-align:center;color:var(--text-faint);">Aucune session</div>';
}

// ==========================================
// RENDU COMPACT DISCOVERY (SCROLLABLE & ACCORDION)
// ==========================================
function renderDiscoverySection(sessions, contextLabel, containerId) {
  if (!sessions || sessions.length === 0) return '';
  const id = containerId || ('disc-' + Math.random().toString(36).substring(2, 9));
  
  return `
    <div style="margin-top:8px;">
      <div class="discovery-accordion-header" onclick="toggleAccordion('${id}')">
        <div style="display:flex; align-items:center; gap:6px;">
          <span id="icon-${id}">▼</span>
          <span>🔍 ${sessions.length} Exploration(s) Discovery ${contextLabel ? '(' + esc(contextLabel) + ')' : ''}</span>
        </div>
        <span class="badge badge--primary" style="font-size:10.5px;">Progressive Disclosure</span>
      </div>
      <div id="${id}" class="discovery-container-scroll" style="display:block;">
        ${sessions.map(ds => {
          const key = ds.run_id || ds.signature || ('ds_' + Math.random().toString(36).substring(2, 8));
          DISCOVERY_REGISTRY[key] = ds;
          return `
            <div class="discovery-chip-compact" onclick="event.stopPropagation(); inspectDiscoveryByKey('${esc(key)}')">
              <div>
                <div style="font-weight:700; font-size:12.5px; color:var(--text);">
                  🧠 ${esc(ds.entity_name || 'Entité')} <span style="font-size:11px; color:var(--text-faint);">(${esc(ds.entity_role || '?')})</span>
                  ${ds.cache_hit ? '<span class="badge badge--success" style="margin-left:4px; font-size:10px;">⚡ Cache</span>' : ''}
                </div>
                <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">
                  🎯 <b>${esc(ds.data_type || 'Général')}</b> · ${esc(ds.goal || 'Découverte sémantique')}
                </div>
              </div>
              <div style="text-align:right;">
                <span class="badge badge--primary">${(ds.steps || []).length} étape(s)</span>
                <div style="font-size:10.5px; color:var(--primary); font-weight:700; margin-top:2px;">Inspecter ➔</div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

// ==========================================
// CHAT THREAD (VUE SESSION)
// ==========================================
function renderSessionThread(sessionId) {
  const session = DATA.sessions.find(s => s.session_id === sessionId);
  const container = document.getElementById('chat-thread');
  if (!session) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-faint);">Sélectionnez une session</div>';
    return;
  }

  let html = `<div style="margin-bottom:20px; padding-bottom:14px; border-bottom:1px solid var(--border);">
    <h2 style="font-size:18px; font-weight:800;">Discussion — <span style="font-family:var(--mono);font-size:14px;color:var(--text-muted);">${esc(sessionId)}</span></h2>
  </div>`;

  const turns = session.turns.slice().sort((a,b) => (a.ts || 0) - (b.ts || 0));

  turns.forEach((t, i) => {
    const tsStr = formatTimestamp(t.ts);
    const uMsg = t.user_message || '(Message vide)';
    const isLongUMsg = uMsg.length > 320;
    const bubbleId = `ububble-${sessionId}-${i}`;
    const escUMsg = esc(uMsg);

    html += `<div class="chat-turn">
      <!-- User Message -->
      <div class="chat-turn__user-row">
        <span class="chat-turn__time">${tsStr}</span>
        <div class="chat-bubble-user" id="${bubbleId}" style="cursor:pointer;" onclick="inspectUserTurn('${esc(sessionId)}', ${i})">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px;">
            <span style="font-size:10.5px; text-transform:uppercase; font-weight:800; opacity:0.8; font-family:var(--mono);">Utilisateur</span>
            <button class="btn-copy-bubble" onclick="event.stopPropagation(); copyTextToClipboard(this, ${JSON.stringify(uMsg)})" title="Copier le texte complet">📋 Copier</button>
          </div>
          <div class="chat-bubble-user__content ${isLongUMsg ? 'collapsed-text' : ''}" id="ucontent-${bubbleId}">${escUMsg}</div>
          ${isLongUMsg ? `
            <div class="chat-bubble-user__toggle" id="utoggle-${bubbleId}" onclick="event.stopPropagation(); toggleUserBubbleText('${bubbleId}', ${uMsg.length})">
              ▼ Afficher tout (${uMsg.length} caractères)
            </div>
          ` : ''}
        </div>
      </div>`;

    if (t.mode === 'direct') {
      html += `<div class="chat-bubble-bot">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
          <div class="bot-header-tag">🧭 Orchestrateur · Réponse Directe</div>
          ${t._routing_call ? `
            <button class="badge badge--primary" style="cursor:pointer;" onclick="inspectTurnRouting('${esc(sessionId)}', ${i})">🔍 Décision Routage LLM (${formatDuration(t._routing_call.duration_ms)})</button>
          ` : ''}
        </div>
        <div style="font-size:14.5px; color:var(--text); line-height:1.5; white-space:pre-wrap; margin-top:4px;">${esc(t.response || '')}</div>`;
        
      if (t._discovery_sessions && t._discovery_sessions.length > 0) {
        html += renderDiscoverySection(t._discovery_sessions, 'Orchestrateur', `turn-disc-${i}`);
      }

      html += `</div>`;
    } else {
      const ep = findEpisode(t.mission_id);
      const epStatus = ep ? ep.status : 'pending';
      const goal = t.refined_goal || (ep && ep.goal) || 'Mission déclenchée';

      html += `<div class="mission-banner" onclick="openMission('${esc(t.mission_id)}')">
        <div class="mission-banner__header">
          <div class="bot-header-tag">🚀 Mission Déléguée</div>
          <div>${statusBadge(epStatus)}</div>
        </div>
        <div class="mission-banner__title">${esc(goal)}</div>
        <div class="mission-banner__footer">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            <span>${t.signatures && t.signatures.length ? `🎯 ${t.signatures.map(s => s.action + ' ' + s.object).join(', ')}` : 'Pipeline Hiérarchique HTN'}</span>
            ${t._routing_call ? `
              <button class="badge badge--purple" style="cursor:pointer;" onclick="event.stopPropagation(); inspectTurnRouting('${esc(sessionId)}', ${i})">🔍 Décision Routage LLM (${formatDuration(t._routing_call.duration_ms)})</button>
            ` : ''}
          </div>
          <span style="font-weight:800;">Explorer le cycle complet ➔</span>
        </div>`;

      if (t._discovery_sessions && t._discovery_sessions.length > 0) {
        html += `<div style="margin-top:10px; border-top:1px dashed var(--border); padding-top:8px;" onclick="event.stopPropagation();">
          ${renderDiscoverySection(t._discovery_sessions, 'Orchestrateur', `turn-disc-${i}`)}
        </div>`;
      }

      html += `</div>`;
    }

    html += `</div>`;
  });

  container.innerHTML = html;
}

// ==========================================
// MISSION DETAIL & HIERARCHICAL PIPELINE
// ==========================================
function renderMissionDetail(missionId) {
  const ep = findEpisode(missionId);
  const pane = document.getElementById('mission-tree-pane');
  if (!ep) {
    pane.innerHTML = '<div style="padding:40px;text-align:center;">Mission introuvable.</div>';
    return;
  }

  let html = `<div>
    <button class="back-btn" onclick="backToSession()">← Retour au fil de discussion</button>
    
    <!-- Mission Header Card -->
    <div style="background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:18px 22px; margin-bottom:18px; box-shadow:var(--shadow-xs);">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px;">
        <div>
          <div style="font-size:11px; font-weight:800; color:var(--text-faint); text-transform:uppercase; font-family:var(--mono);">Mission ID: ${esc(ep.mission_id)}</div>
          <h1 style="font-size:19px; font-weight:800; margin-top:4px; color:var(--text);">${esc(ep.goal)}</h1>
        </div>
        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
          <div>${statusBadge(ep.status)}</div>
          ${ep._routing_call ? `
            <button class="badge badge--purple" style="cursor:pointer;" onclick="inspectMissionRouting('${esc(ep.mission_id)}')">🔍 Décision Routage Orchestrateur (${formatDuration(ep._routing_call.duration_ms)})</button>
          ` : ''}
        </div>
      </div>
      <div style="display:flex; gap:16px; margin-top:12px; font-size:12px; color:var(--text-muted); font-family:var(--mono); flex-wrap:wrap;">
        <span>🕒 Début: ${formatTimestamp(ep.created_at)}</span>
        <span>🏁 Fin: ${formatTimestamp(ep.finished_at)}</span>
        <span>🌍 Env: <b>${esc(ep.environment || 'simulated')}</b></span>
      </div>
    </div>`;

  // Root Signatures
  if (ep.signatures && ep.signatures.length > 0) {
    html += `<div style="margin-bottom:14px; display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
      <span style="font-size:12px; font-weight:700; color:var(--text-muted);">Signatures Racine :</span>`;
    ep.signatures.forEach(s => {
      html += `<span class="badge badge--purple">${esc(s.action + ' ' + s.object)} ${s.desired_state ? '➔ ' + esc(s.desired_state) : ''}</span>`;
    });
    html += `</div>`;
  }

  // HTN Solver Execution Tree & Cognitive Phases
  html += `<div style="margin-top:18px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <div style="font-size:13.5px; font-weight:800; text-transform:uppercase; letter-spacing:0.04em; color:var(--text-muted);">
        🌳 Pipeline & Cycle Cognitif des Solvers
      </div>
      <button class="badge badge--primary" style="cursor:pointer;" onclick="toggleAllAttempts()">Tout Déplier / Replier</button>
    </div>`;

  if (ep.execution_tree && ep.execution_tree.solver_id) {
    html += renderSolverNodeModern(ep, ep.execution_tree, 0);
  } else {
    html += '<div style="padding:20px;background:var(--surface);border-radius:10px;text-align:center;color:var(--text-faint);">Aucun arbre d\'exécution</div>';
  }

  html += `</div>`;

  // Discovery Sessions (Global Mission level)
  const globalDiscovery = (ep._discovery_sessions || []).filter(s => s.caller !== 'presentator' && !s.solver_id && !s.step_id);
  if (globalDiscovery.length > 0) {
    html += `<div style="margin-top:24px;">
      <div style="font-size:13.5px; font-weight:800; text-transform:uppercase; letter-spacing:0.04em; color:var(--text-muted); margin-bottom:10px;">
        🔍 Sessions Discovery Globales (Mission)
      </div>
      ${renderDiscoverySection(globalDiscovery, 'Global Mission', 'global-mission-disc')}
    </div>`;
  }

  // Presentator Block
  if (ep.presentator_result || (ep._presentator_calls && ep._presentator_calls.length > 0)) {
    html += `<div style="margin-top:24px;">
      <div style="font-size:13.5px; font-weight:800; text-transform:uppercase; letter-spacing:0.04em; color:var(--text-muted); margin-bottom:10px;">
        🗣️ Presentator (Rapport Final)
      </div>
      <div class="tree-node-card" style="border-left:4px solid #f59e0b;" onclick="inspectPresentator('${esc(ep.mission_id)}')">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="font-weight:700;">Génération du rapport utilisateur</div>
          ${ep.presentator_result ? statusBadge(ep.presentator_result.status) : ''}
        </div>
        <div style="font-size:13px; color:var(--text-muted); margin-top:4px;">Cliquez pour voir la prompt et la formulation finale.</div>
      </div>`;

    if (ep._presentator_discovery && ep._presentator_discovery.length > 0) {
      html += renderDiscoverySection(ep._presentator_discovery, 'Presentator', 'presentator-disc');
    }

    html += `</div>`;
  }

  html += `</div>`;
  pane.innerHTML = html;
}

// ==========================================
// RENDU DU CYCLE DE VIE D'UN SOLVER (COMPLET)
// ==========================================
function renderSolverNodeModern(ep, treeNode, depth) {
  const solverId = treeNode.solver_id;
  if (!solverId) return '';

  const cleanSid = solverId.replace(/^solver_/, '');
  const prepCalls = (ep._solver_feasibility && (ep._solver_feasibility[solverId] || ep._solver_feasibility[cleanSid])) || [];
  const feasCalls = prepCalls.filter(c => c.tag === 'FeasibilityDecision');
  const sigCalls = (ep._solver_signatures && (ep._solver_signatures[solverId] || ep._solver_signatures[cleanSid])) || [];
  const compactorCalls = (ep._solver_compactor && (ep._solver_compactor[solverId] || ep._solver_compactor[cleanSid])) || [];
  const retrievalEvents = (ep._solver_retrieval && (ep._solver_retrieval[solverId] || ep._solver_retrieval[cleanSid] || (depth === 0 ? ep._solver_retrieval['root_solver'] : []))) || [];
  
  const lastFeas = feasCalls[feasCalls.length - 1];
  
  // Analyse robuste de la faisabilité
  const isFeasible = lastFeas?.response ? (lastFeas.response.is_possible !== false && lastFeas.response.feasible !== false) : true;
  const feasReason = lastFeas?.response?.reason || lastFeas?.response?.rationale || '';
  const refinedStrategy = lastFeas?.response?.refined_strategy || '';

  const planningCalls = (ep._solver_planning && (ep._solver_planning[solverId] || ep._solver_planning[cleanSid])) || [];
  const validationCalls = (ep._solver_validation && (ep._solver_validation[solverId] || ep._solver_validation[cleanSid])) || [];
  const learnerCalls = (ep._solver_learner && (ep._solver_learner[solverId] || ep._solver_learner[cleanSid])) || [];
  const postCalls = [...compactorCalls, ...learnerCalls];

  // Détection du rejet Superviseur
  let supervisorRejected = false;
  let supervisorCritique = '';
  validationCalls.forEach(vc => {
    const resp = vc.response || {};
    if (resp.approved === false || resp.is_valid === false || resp.status === 'rejected') {
      supervisorRejected = true;
      supervisorCritique = resp.critique || resp.reason || resp.feedback || 'Le plan proposé a été invalidé par les règles de supervision.';
    }
  });

  // Discovery rattaché à ce solver spécifique
  const solverDiscovery = (ep._solver_discovery && (ep._solver_discovery[solverId] || ep._solver_discovery[cleanSid])) || [];

  // Signatures pour les sous-solvers
  let solverSignatures = treeNode.signatures || [];
  if (solverSignatures.length === 0 && sigCalls.length > 0) {
    const lastSig = sigCalls[sigCalls.length - 1];
    if (Array.isArray(lastSig?.response?.signatures)) {
      solverSignatures = lastSig.response.signatures;
    } else if (Array.isArray(lastSig?.response)) {
      solverSignatures = lastSig.response;
    }
  }

  let html = `<div class="solver-block" style="margin-bottom:18px; margin-left:${depth > 0 ? '16px' : '0'};">`;

  // 1. Solver Header Card
  const isRejectedState = (!isFeasible) || supervisorRejected || (treeNode.status === 'failed');
  html += `<div class="tree-node-card tree-node-card--solver ${isRejectedState ? 'tree-node-card--rejected' : ''}" onclick="inspectSolver('${esc(ep.mission_id)}', '${esc(solverId)}')">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <span style="font-weight:800; font-size:13.5px; color:var(--purple); font-family:var(--mono);">🧠 SOLVER [${esc(solverId)}]</span>
        ${depth > 0 ? '<span class="badge badge--purple">Sous-Solver</span>' : '<span class="badge badge--primary">Root Solver</span>'}
        ${treeNode.status ? statusBadge(treeNode.status) : ''}
      </div>
      <button class="badge badge--primary" style="cursor:pointer;" onclick="event.stopPropagation(); inspectSolver('${esc(ep.mission_id)}', '${esc(solverId)}')">Détails Solver ➔</button>
    </div>
    <div style="font-size:14px; font-weight:700; margin-top:6px; color:var(--text);">${esc(treeNode.goal || 'Objectif du solver')}</div>
  </div>`;

  // Discovery au niveau Solver
  if (solverDiscovery.length > 0) {
    html += `<div style="margin-left:14px; margin-bottom:10px;">
      ${renderDiscoverySection(solverDiscovery, `Solver ${solverId}`, `solver-disc-${solverId}`)}
    </div>`;
  }

  // SÉQUENCE COGNITIVE AVANT PLANIFICATION (Signatures ➔ Retriever ➔ Compactor ➔ Faisabilité)
  html += `<div style="margin-left:14px; margin-bottom:12px; display:flex; flex-direction:column; gap:8px;">`;

  // Phase A : SignatureExtractor
  if (sigCalls.length > 0 || solverSignatures.length > 0) {
    html += `<div class="tree-node-card tree-node-card--prep" style="padding:10px 14px; cursor:pointer;" onclick="inspectSignatureCalls('${esc(ep.mission_id)}', '${esc(solverId)}')">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
        <div style="font-weight:800; font-size:12.5px; color:var(--purple); display:flex; align-items:center; gap:6px;">
          <span>🎯 SignatureExtractor</span>
          <span class="badge badge--purple">${sigCalls.length} appel(s)</span>
        </div>
        ${sigCalls[0]?.duration_ms ? `<span style="font-size:11px; font-family:var(--mono); color:var(--text-faint);">${formatDuration(sigCalls[0].duration_ms)}</span>` : ''}
      </div>
      <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
        ${solverSignatures.map(s => `<span class="badge badge--purple">${esc((s.action || '') + ' ' + (s.object || ''))} ${s.desired_state ? '➔ ' + esc(s.desired_state) : ''}</span>`).join('')}
      </div>
    </div>`;
  }

  // Phase B : Retriever (Recherche de missions similaires)
  if (retrievalEvents.length > 0 || (treeNode.signatures && treeNode.signatures.length > 0) || (sigCalls.length > 0)) {
    let allMatches = [];
    retrievalEvents.forEach(ev => {
      const mList = ev.results || ev.matches || ev.episodes;
      if (Array.isArray(mList)) {
        allMatches.push(...mList);
      } else if (ev.found_mission_id || ev.result_mission_id || ev.goal || ev.result_goal) {
        allMatches.push({
          mission_id: ev.found_mission_id || ev.result_mission_id,
          goal: ev.goal || ev.result_goal,
          score: ev.score,
          similarity: ev.similarity,
          summary: ev.summary
        });
      }
    });

    // Dédupliquer par mission_id si présent
    const seenMids = new Set();
    const uniqueMatches = [];
    for (const m of allMatches) {
      const mid = m.mission_id || m.id;
      if (mid && !seenMids.has(mid)) {
        seenMids.add(mid);
        uniqueMatches.push(m);
      } else if (!mid) {
        uniqueMatches.push(m);
      }
    }

    html += `<div class="tree-node-card tree-node-card--prep" style="padding:10px 14px; border-left:4px solid var(--primary); cursor:pointer;" onclick="inspectRetriever('${esc(ep.mission_id)}', '${esc(solverId)}')">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
        <div style="font-weight:800; font-size:12.5px; color:var(--primary); display:flex; align-items:center; gap:6px;">
          <span>🔎 Retriever (Mémoire Épisodique)</span>
          <span class="badge ${uniqueMatches.length > 0 ? 'badge--primary' : 'badge--neutral'}">${uniqueMatches.length} similaire(s)</span>
        </div>
        <span style="font-size:11px; font-family:var(--mono); color:var(--text-faint);">Score K-NN / Cosine</span>
      </div>
      <div style="margin-top:6px; display:flex; flex-direction:column; gap:4px;">
        ${uniqueMatches.length > 0 ? uniqueMatches.slice(0, 3).map(m => {
          const matchMid = m.mission_id || m.id;
          const score = typeof m.score === 'number' ? `(sim: ${(m.score * 100).toFixed(0)}%)` : '';
          return `
            <div style="display:flex; justify-content:space-between; align-items:center; font-size:12px; background:var(--surface-alt); padding:4px 8px; border-radius:6px;">
              <span style="color:var(--text); font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:70%;">${esc(m.goal || matchMid)}</span>
              <button class="badge badge--primary" style="cursor:pointer;" onclick="event.stopPropagation(); openMission('${esc(matchMid)}');">Voir Mission ${esc(matchMid)} ${score} ➔</button>
            </div>
          `;
        }).join('') : `
          <div style="font-size:12px; color:var(--text-muted); font-style:italic;">
            Aucune mission passée suffisamment proche (seuil cosine non atteint). Démarrage en mode exploration fraîche.
          </div>
        `}
      </div>
    </div>`;
  }

  // Phase C : MissionCompactor
  if (compactorCalls.length > 0) {
    html += `<div class="tree-node-card tree-node-card--prep" style="padding:10px 14px; border-left:4px solid #8b5cf6; cursor:pointer;" onclick="inspectCompactor('${esc(ep.mission_id)}', '${esc(solverId)}')">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
        <div style="font-weight:800; font-size:12.5px; color:var(--purple); display:flex; align-items:center; gap:6px;">
          <span>📦 MissionCompactor (Synthèse & Contexte)</span>
          <span class="badge badge--purple">${compactorCalls.length} appel(s)</span>
        </div>
        ${compactorCalls[0]?.duration_ms ? `<span style="font-size:11px; font-family:var(--mono); color:var(--text-faint);">${formatDuration(compactorCalls[0].duration_ms)}</span>` : ''}
      </div>
      <div style="margin-top:4px; font-size:12px; color:var(--text-muted);">
        Compression et mise à disposition des résumés contextuels pour le Solver.
      </div>
    </div>`;
  }

  // Phase D : FeasibilityDecision
  if (feasCalls.length > 0) {
    html += `<div class="tree-node-card ${isFeasible ? 'tree-node-card--prep' : 'tree-node-card--rejected'}" style="padding:10px 14px; cursor:pointer;" onclick="inspectFeasibility('${esc(ep.mission_id)}', '${esc(solverId)}')">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
        <div style="font-weight:800; font-size:12.5px; display:flex; align-items:center; gap:6px;">
          <span>⚖️ Feasibility Decision</span>
          <span class="badge ${isFeasible ? 'badge--success' : 'badge--failed'}">${isFeasible ? '✅ FAISABLE' : '❌ REJETÉ / INFAISABLE'}</span>
        </div>
        ${lastFeas?.duration_ms ? `<span style="font-size:11px; font-family:var(--mono); color:var(--text-faint);">${formatDuration(lastFeas.duration_ms)}</span>` : ''}
      </div>
      ${feasReason ? `<div style="margin-top:4px; font-size:12px; color:${isFeasible ? 'var(--text-muted)' : 'var(--failure)'};"><b>${isFeasible ? 'Raison :' : 'Motif de rejet :'}</b> ${esc(feasReason)}</div>` : ''}
      ${refinedStrategy ? `<div style="margin-top:4px; font-size:12px; color:var(--text);"><b>🎯 Stratégie raffinée :</b> ${esc(refinedStrategy)}</div>` : ''}
    </div>`;
  }

  html += `</div>`;

  // Bannière Rejet Superviseur si applicable
  if (supervisorRejected) {
    html += `<div class="rejection-banner" style="margin-left:14px; margin-bottom:12px;">
      <div class="rejection-banner__title">⚠️ Dernier Plan invalidé par le Superviseur</div>
      <div>${esc(supervisorCritique)}</div>
    </div>`;
  }

  // 3. Tentatives Déroulables / Pliables (Calcul de Statut Corrigé)
  const attempts = treeNode.attempts || [];
  if (attempts.length > 0) {
    attempts.forEach(att => {
      const attNum = att.attempt_number ?? 0;
      const nodes = att.nodes || [];
      const totalNodes = nodes.length;
      const succCount = nodes.filter(n => n.status === 'success').length;
      const skippedCount = nodes.filter(n => n.status === 'skipped').length;
      const failedCount = nodes.filter(n => n.status === 'failed').length;

      // Logique robuste de statut de tentative
      let attStatus = att.outcome || att.status || "in_progress";
      if (!attStatus || attStatus === 'pending') {
        if (failedCount > 0) {
          attStatus = 'failed';
        } else if (totalNodes > 0 && (succCount + skippedCount === totalNodes)) {
          attStatus = 'success';
        } else if (att.status) {
          attStatus = att.status;
        } else {
          attStatus = 'success';
        }
      } else if (attStatus === 'failed' && failedCount === 0 && (succCount + skippedCount === totalNodes)) {
        attStatus = 'success';
      }

      const attemptId = `att-${esc(solverId)}-${attNum}`;

      html += `<div class="attempt-accordion">
        <div class="attempt-header" onclick="toggleAccordion('${attemptId}')">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            <span id="icon-${attemptId}">▼</span>
            <span>Tentative #${attNum} (${succCount} réussie${succCount > 1 ? 's' : ''}${skippedCount > 0 ? `, ${skippedCount} ignorée${skippedCount > 1 ? 's' : ''}` : ''}${failedCount > 0 ? `, ${failedCount} échouée${failedCount > 1 ? 's' : ''}` : ''} / ${totalNodes} étapes)</span>
            ${statusBadge(attStatus)}
          </div>
          <span style="font-size:11px; font-family:var(--mono); color:var(--text-faint);">Détails</span>
        </div>

        <div id="${attemptId}" style="display:block;">`;

      // Affichage du Planner et du Validator pour CETTE tentative
      const attPlanning = att._planning_calls || [];
      const attValidation = att._validation_calls || [];
      
      if (attPlanning.length > 0 || attValidation.length > 0) {
        html += `<div style="margin-left:8px; margin-bottom:12px; display:flex; flex-direction:column; gap:6px;">
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
          ${attPlanning.map((c, pIdx) => `
            <button class="tree-node-card tree-node-card--plan" style="padding:6px 12px; font-size:11px; font-weight:700; cursor:pointer;" onclick="event.stopPropagation(); inspectAttemptPlanningCall('${esc(ep.mission_id)}', '${esc(solverId)}', ${attNum}, ${pIdx})">
              📐 Planner LLM (${formatDuration(c.duration_ms)})
            </button>
          `).join('')}
          ${attValidation.map((c, vIdx) => {
            const resp = c.response || {};
            const isAppr = resp.is_conformant !== false && resp.approved !== false && resp.is_valid !== false;
            return `
              <button class="tree-node-card ${isAppr ? 'tree-node-card--validation' : 'tree-node-card--rejected'}" style="padding:6px 12px; font-size:11px; font-weight:700; cursor:pointer;" onclick="event.stopPropagation(); inspectAttemptValidationCall('${esc(ep.mission_id)}', '${esc(solverId)}', ${attNum}, ${vIdx})">
                ${isAppr ? '⚖️ Supervisor: Plan Validé' : '❌ Supervisor: REJET'} (${formatDuration(c.duration_ms)})
              </button>
            `;
          }).join('')}
          </div>`;
          
          // Si le superviseur a rejeté, afficher directement la raison
          attValidation.forEach(c => {
             const resp = c.response || {};
             const isAppr = resp.is_conformant !== false && resp.approved !== false && resp.is_valid !== false;
             if (!isAppr && (resp.critique || resp.reason || resp.feedback)) {
                html += `<div style="background:var(--surface-alt); padding:8px 12px; border-radius:6px; border-left:3px solid var(--failure); font-size:12px; color:var(--text); margin-top:4px;">
                  <b>Raison du rejet :</b> ${esc(resp.critique || resp.reason || resp.feedback)}
                </div>`;
             }
          });
          
        html += `</div>`;
      }

      nodes.forEach((node, nodeIdx) => {
        const stepDisc = node._discovery_sessions || [];
        const isStepFailed = node.status === 'failed' || !!node.error_reason;

        html += `<div class="tree-node-card ${isStepFailed ? 'tree-node-card--rejected' : 'tree-node-card--step'}" style="margin-left: 8px;" onclick="inspectStep('${esc(ep.mission_id)}', '${esc(solverId)}', ${attNum}, '${esc(node.step_id)}')">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-family:var(--mono); font-size:11.5px; font-weight:800; color:var(--primary);">#${nodeIdx + 1} · ${esc(node.step_id)}</span>
              ${node.tool_name ? `<span class="badge badge--primary">🔧 ${esc(node.tool_name)}</span>` : ''}
              <span style="font-size:11px; color:var(--text-faint); font-family:var(--mono);">${esc(node.step_type || '')}</span>
            </div>
            <div>${statusBadge(node.status)}</div>
          </div>
          <div style="font-size:13px; font-weight:600; color:var(--text); margin-top:6px;">${esc(node.description || 'Étape sans description')}</div>
          
          ${stepDisc.length > 0 ? `
            <div style="margin-top:6px; display:flex; align-items:center; gap:6px;" onclick="event.stopPropagation();">
              ${stepDisc.map(sd => {
                const sKey = sd.run_id || sd.signature || ('sd_' + Math.random().toString(36).substring(2, 8));
                DISCOVERY_REGISTRY[sKey] = sd;
                return `
                  <span class="badge badge--primary" style="cursor:pointer;" onclick="inspectDiscoveryByKey('${esc(sKey)}')">
                    🔍 Discovery: ${esc(sd.entity_name)} (${(sd.steps || []).length} étapes)
                  </span>
                `;
              }).join('')}
            </div>
          ` : ''}

          ${node.error_reason ? `
            <div style="margin-top:6px; font-size:12px; color:var(--failure); font-weight:600; background:var(--failure-bg); padding:6px 10px; border-radius:6px; border:1px solid var(--failure-border);">
              ❌ <b>Erreur :</b> ${esc(node.error_reason)}
            </div>
          ` : ''}
        </div>`;

        // Si c'est une sous-tâche abstraite qui a un sous-solver enfant (Déroulable / Repliable)
        if (node.child_execution_tree) {
          const subId = `subsolver-${esc(solverId)}-${esc(node.step_id)}`;

          html += `<div class="sub-solver-accordion">
            <div class="sub-solver-header" onclick="toggleAccordion('${subId}')">
              <div style="display:flex; align-items:center; gap:8px;">
                <span id="icon-${subId}">▼</span>
                <span style="color:var(--purple); font-family:var(--mono);">↳ Tâche Abstraite [${esc(node.step_id)}]</span>
                <span style="font-size:12px; font-weight:700; color:var(--text);">${esc(node.child_execution_tree.goal || node.description)}</span>
              </div>
              <div style="display:flex; align-items:center; gap:6px;">
                ${statusBadge(node.child_execution_tree.status || node.status)}
                <span style="font-size:10.5px; font-family:var(--mono); color:var(--text-faint);">Sous-Solver</span>
              </div>
            </div>
            <div id="${subId}" style="display:block;">
              ${renderSolverNodeModern(ep, node.child_execution_tree, depth + 1)}
            </div>
          </div>`;
        }
      });

      // 4. Convergence Calls pour cette tentative
      const attConvergence = att._convergence_calls || [];
      if (attConvergence.length > 0) {
        html += `<div style="margin-left:8px; margin-top:12px; margin-bottom:12px; display:flex; gap:8px; flex-wrap:wrap;">
          ${attConvergence.map((c, cIdx) => {
             const resp = c.response || {};
             const isConv = resp.is_convergent;
             return `
              <button class="tree-node-card ${isConv ? 'tree-node-card--validation' : 'tree-node-card--rejected'}" style="padding:6px 12px; font-size:11px; font-weight:700; cursor:pointer;" onclick="event.stopPropagation(); inspectAttemptConvergenceCall('${esc(ep.mission_id)}', '${esc(solverId)}', ${attNum}, ${cIdx})">
                ${isConv ? '🎯 Convergence OK' : '🎯 Convergence ÉCHOUÉE'} (${formatDuration(c.duration_ms)})
              </button>
             `;
          }).join('')}
        </div>`;
      }

      html += `</div></div>`;
    });
  }

  // 4. Post-Exécution (Learner & Synthèse)
  if (learnerCalls.length > 0) {
    html += `<div style="margin-left:14px; margin-top:6px; display:flex; gap:8px; flex-wrap:wrap;">
      ${learnerCalls.map((c, lIdx) => `
        <button class="tree-node-card tree-node-card--post" style="padding:6px 12px; margin-bottom:4px; font-size:12px; font-weight:700; cursor:pointer;" onclick="inspectLearnerCall('${esc(ep.mission_id)}', '${esc(solverId)}', ${lIdx})">
          🎓 Learner (${formatDuration(c.duration_ms)})
        </button>
      `).join('')}
    </div>`;
  }

  html += `</div>`;
  return html;
}

function toggleAccordion(id) {
  const el = document.getElementById(id);
  const icon = document.getElementById(`icon-${id}`);
  if (!el) return;
  if (el.style.display === 'none') {
    el.style.display = 'block';
    if (icon) icon.textContent = '▼';
  } else {
    el.style.display = 'none';
    if (icon) icon.textContent = '▶';
  }
}

let allExpanded = true;
function toggleAllAttempts() {
  allExpanded = !allExpanded;
  // Accordéons de tentatives
  document.querySelectorAll('.attempt-accordion > div[id^="att-"]').forEach(el => {
    el.style.display = allExpanded ? 'block' : 'none';
  });
  document.querySelectorAll('.attempt-accordion span[id^="icon-att-"]').forEach(icon => {
    icon.textContent = allExpanded ? '▼' : '▶';
  });

  // Accordéons de sous-solvers
  document.querySelectorAll('.sub-solver-accordion > div[id^="subsolver-"]').forEach(el => {
    el.style.display = allExpanded ? 'block' : 'none';
  });
  document.querySelectorAll('.sub-solver-accordion span[id^="icon-subsolver-"]').forEach(icon => {
    icon.textContent = allExpanded ? '▼' : '▶';
  });
}

// ==========================================
// INSPECTEUR LATÉRAL (PANNEAU PERSISTANT)
// ==========================================
function updateInspector(title, badge, htmlOverview, promptCalls = [], rawJson = null) {
  toggleInspector(true); // Open inspector panel if it was collapsed
  document.getElementById('inspector-title').textContent = title;
  document.getElementById('inspector-badge').textContent = badge;
  document.getElementById('inspector-tabs').style.display = 'flex';
  
  selectedInspectorData = {
    overview: htmlOverview,
    prompt: promptCalls,
    raw: rawJson
  };
  
  showInspectorTab(activeInspectorTab);
}

function showInspectorTab(tabName) {
  activeInspectorTab = tabName;
  document.querySelectorAll('.inspector-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });

  const body = document.getElementById('inspector-body');
  if (!selectedInspectorData) return;

  if (tabName === 'overview') {
    body.innerHTML = selectedInspectorData.overview;
  } else if (tabName === 'prompt') {
    const calls = selectedInspectorData.prompt || [];
    if (calls.length === 0) {
      body.innerHTML = '<div style="color:var(--text-faint);text-align:center;padding:40px;">Aucun appel LLM direct enregistré sur cet élément.</div>';
    } else {
      body.innerHTML = calls.map(c => `
        <div style="margin-bottom:18px; border:1px solid var(--border); border-radius:10px; overflow:hidden;">
          <div style="background:var(--surface-alt); padding:9px 12px; font-size:12px; font-weight:800; font-family:var(--mono); display:flex; justify-content:space-between;">
            <span>${esc(c.tag || c.schema || 'LLM Call')}</span>
            <span style="color:var(--text-faint);">${formatDuration(c.duration_ms)}</span>
          </div>
          <div style="padding:12px;">
            <div style="font-size:11px; font-weight:800; color:var(--text-faint); margin-bottom:4px; text-transform:uppercase;">Prompt Système & Utilisateur</div>
            <div class="code-box">${esc(c.prompt || '(Vide)')}</div>
            
            <div style="font-size:11px; font-weight:800; color:var(--text-faint); margin:10px 0 4px; text-transform:uppercase;">Réponse LLM Structurée</div>
            <div class="code-box" style="background:#1e293b;">${esc(fmtJson(c.response || c.raw_response || ''))}</div>
          </div>
        </div>
      `).join('');
    }
  } else if (tabName === 'raw') {
    body.innerHTML = `<div class="code-box">${esc(fmtJson(selectedInspectorData.raw || {}))}</div>`;
  }
}

document.querySelectorAll('.inspector-tab-btn').forEach(btn => {
  btn.addEventListener('click', () => showInspectorTab(btn.dataset.tab));
});

// INSPECTION HANDLERS
function inspectUserTurn(sessionId, turnIdx) {
  const session = DATA.sessions.find(s => s.session_id === sessionId);
  const turn = session?.turns[turnIdx];
  if (!turn) return;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Message Utilisateur</div>
      <div style="font-size:15px; font-weight:700; color:var(--text); margin-top:4px;">${esc(turn.user_message)}</div>
    </div>
    <div style="font-size:12px; font-family:var(--mono); color:var(--text-muted);">
      <div>Horodatage: ${formatTimestamp(turn.ts)}</div>
      <div>Mode: <b>${esc(turn.mode)}</b></div>
      ${turn.mission_id ? `<div>Mission ID: ${esc(turn.mission_id)}</div>` : ''}
    </div>
  </div>`;

  updateInspector('Tour Utilisateur', 'Message', overview, turn._routing_call ? [turn._routing_call] : [], turn);
}

function inspectTurnRouting(sessionId, turnIdx) {
  const session = DATA.sessions.find(s => s.session_id === sessionId);
  const turn = session?.turns[turnIdx];
  const call = turn?._routing_call;
  const isMission = turn?.mode === 'mission' || (call?.response && (call.response.mode === 'mission' || call.response.decision === 'mission'));

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Décision de Routage Orchestrateur</div>
      <div style="font-size:14px; margin-top:4px; color:var(--text);">
        L'orchestrateur a qualifié la requête utilisateur pour décider de la délégation (directe vs mission autonome).
      </div>
    </div>
    
    <div style="background:var(--surface-alt); padding:12px; border-radius:8px; font-size:13px; border:1px solid var(--border);">
      <div><b>Mode Qualifié :</b> <span class="badge ${isMission ? 'badge--purple' : 'badge--primary'}">${isMission ? '🚀 MISSION AUTONOME' : '💬 RÉPONSE DIRECTE'}</span></div>
      ${turn?.mission_id ? `<div style="margin-top:6px; font-family:var(--mono); font-size:12px;"><b>Mission ID :</b> ${esc(turn.mission_id)}</div>` : ''}
      ${call?.response?.reason ? `<div style="margin-top:8px;"><b>Raisonnement :</b> ${esc(call.response.reason)}</div>` : ''}
      ${(call?.response?.refined_goal || turn?.refined_goal) ? `<div style="margin-top:6px;"><b>Objectif Raffiné :</b> ${esc(call?.response?.refined_goal || turn?.refined_goal)}</div>` : ''}
      ${turn?.signatures && turn.signatures.length > 0 ? `
        <div style="margin-top:8px;">
          <b>Signatures Extraites :</b>
          <div style="margin-top:4px; display:flex; gap:4px; flex-wrap:wrap;">
            ${turn.signatures.map(s => `<span class="badge badge--purple">${esc(s.action + ' ' + s.object)}</span>`).join('')}
          </div>
        </div>
      ` : ''}
    </div>
  </div>`;

  updateInspector(isMission ? 'Orchestrateur: Mission' : 'Orchestrateur: Direct', isMission ? 'Mission' : 'Direct', overview, call ? [call] : [], call || turn);
}

function inspectMissionRouting(missionId) {
  const ep = findEpisode(missionId);
  const call = ep?._routing_call;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Décision de Routage Initiale (Mission)</div>
      <div style="font-size:14px; margin-top:4px; color:var(--text);">Analyse et qualification de la mission autonome par l'Orchestrateur.</div>
    </div>
    
    <div style="background:var(--surface-alt); padding:12px; border-radius:8px; font-size:13px; border:1px solid var(--border);">
      <div><b>Mode :</b> <span class="badge badge--purple">🚀 MISSION AUTONOME</span></div>
      <div style="margin-top:6px; font-family:var(--mono); font-size:12px;"><b>Mission ID :</b> ${esc(missionId)}</div>
      ${call?.response?.reason ? `<div style="margin-top:8px;"><b>Raisonnement :</b> ${esc(call.response.reason)}</div>` : ''}
      ${(call?.response?.refined_goal || ep?.refined_goal || ep?.goal) ? `<div style="margin-top:6px;"><b>Objectif Raffiné :</b> ${esc(call?.response?.refined_goal || ep?.refined_goal || ep?.goal)}</div>` : ''}
    </div>
  </div>`;

  updateInspector(`Orchestrateur: Mission`, 'Mission', overview, call ? [call] : [], call || ep);
}

function inspectDiscoveryByKey(key) {
  const session = DISCOVERY_REGISTRY[key];
  if (!session) return;

  const steps = session.steps || [];

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Entité Explorée & Rôle</div>
      <div style="font-size:16px; font-weight:800; color:var(--text);">🧠 ${esc(session.entity_name)} <span style="font-size:13px; color:var(--text-muted);">(${esc(session.entity_role || '?')})</span></div>
    </div>
    
    <div style="background:var(--primary-bg); border:1px solid var(--primary-border); border-radius:8px; padding:12px;">
      <div style="font-weight:700; font-size:13px; color:var(--primary);">🎯 Cible & Objectif Découverte</div>
      <div style="font-size:12.5px; margin-top:4px;"><b>Type :</b> ${esc(session.data_type || 'Général')}</div>
      <div style="font-size:12.5px; margin-top:2px;"><b>Objectif :</b> ${esc(session.goal || '')}</div>
      <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;"><b>Déclenché par :</b> <span class="badge badge--primary">${esc(session.caller || 'composant')}</span></div>
    </div>

    ${session.summary ? `
      <div style="background:var(--purple-bg); border:1px solid var(--purple-border); border-radius:8px; padding:12px;">
        <div style="font-weight:700; font-size:13px; color:var(--purple);">📝 Résumé & Contexte Acquis</div>
        <div style="font-size:12.5px; margin-top:4px; white-space:pre-wrap;">${esc(session.summary)}</div>
      </div>
    ` : ''}

    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:8px;">Chronologie des Étapes Discovery (${steps.length})</div>
      ${steps.length === 0 ? '<div style="color:var(--text-faint); font-size:12px;">Aucune étape détaillée.</div>' : `
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${steps.map((st, sIdx) => `
            <div style="background:var(--surface-alt); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:12.5px;">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:800; font-family:var(--mono); color:var(--primary);">#${sIdx + 1} · ${esc(st.tool_name || st.step_type || 'step')}</span>
                <span style="font-size:10.5px; color:var(--text-faint); font-family:var(--mono);">${formatTimestamp(st.timestamp)}</span>
              </div>
              ${st.question ? `<div style="margin-top:4px;"><b>Question :</b> ${esc(st.question)}</div>` : ''}
              ${st.description ? `<div style="margin-top:2px; color:var(--text-muted);">${esc(st.description)}</div>` : ''}
              ${st.result && Object.keys(st.result).length > 0 ? `
                <div style="margin-top:6px;" class="code-box">${esc(JSON.stringify(st.result, null, 2))}</div>
              ` : ''}
            </div>
          `).join('')}
        </div>
      `}
    </div>
  </div>`;

  updateInspector(`Discovery: ${session.entity_name}`, 'Discovery', overview, session.explorer_plan_calls || [], session);
}

function inspectSolver(missionId, solverId) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const prepCalls = (ep._solver_feasibility && (ep._solver_feasibility[solverId] || ep._solver_feasibility[cleanSid])) || [];
  const planningCalls = (ep._solver_planning && (ep._solver_planning[solverId] || ep._solver_planning[cleanSid])) || [];
  const validationCalls = (ep._solver_validation && (ep._solver_validation[solverId] || ep._solver_validation[cleanSid])) || [];
  const sigCalls = (ep._solver_signatures && (ep._solver_signatures[solverId] || ep._solver_signatures[cleanSid])) || [];
  const compactorCalls = (ep._solver_compactor && (ep._solver_compactor[solverId] || ep._solver_compactor[cleanSid])) || [];
  const learnerCalls = (ep._solver_learner && (ep._solver_learner[solverId] || ep._solver_learner[cleanSid])) || [];
  const allCalls = [...sigCalls, ...compactorCalls, ...prepCalls, ...planningCalls, ...validationCalls, ...learnerCalls];

  const feasCalls = prepCalls.filter(c => c.tag === 'FeasibilityDecision');
  const registry = (ep._registries && (ep._registries[solverId] || ep._registries[cleanSid])) || {};

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Solver Cognitif</div>
      <div style="font-family:var(--mono); font-weight:800; color:var(--purple); font-size:15px; margin-top:2px;">🧠 ${esc(solverId)}</div>
    </div>`;

  if (feasCalls.length > 0) {
    const f = feasCalls[feasCalls.length - 1];
    const resp = f.response || {};
    const isFeas = resp.is_possible !== false && resp.feasible !== false;
    overview += `<div style="background:var(--surface-alt); padding:12px; border-radius:10px; border-left:4px solid ${isFeas ? 'var(--success)' : 'var(--failure)'}; border:1px solid var(--border);">
      <div style="font-weight:800; font-size:13.5px;">Faisabilité : ${isFeas ? '✅ FAISABLE' : '❌ REJETÉ / INFAISABLE'}</div>
      <div style="font-size:12.5px; margin-top:4px; color:var(--text-muted);">${esc(resp.reason || resp.rationale || '')}</div>
      ${resp.refined_strategy ? `<div style="margin-top:8px; font-size:12.5px;"><b>🎯 Stratégie :</b> ${esc(resp.refined_strategy)}</div>` : ''}
    </div>`;
  }

  if (Object.keys(registry).length > 0) {
    overview += `<div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Registre des Variables Déclarées</div>
      <div class="code-box">${esc(JSON.stringify(registry, null, 2))}</div>
    </div>`;
  }

  overview += `</div>`;

  updateInspector(`Solver: ${solverId}`, 'Solver', overview, allCalls, { solverId, sigCalls, compactorCalls, prepCalls, planningCalls, validationCalls, learnerCalls, registry });
}

function inspectSignatureCalls(missionId, solverId) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_signatures && (ep._solver_signatures[solverId] || ep._solver_signatures[cleanSid])) || [];
  const sigs = calls[calls.length - 1]?.response?.signatures || calls[calls.length - 1]?.response || [];

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Signature Extractor</div>
      <div style="font-size:14px; margin-top:4px;">Extraction formelle des signatures d'action (action, objet, état désiré) pour le Solver <b>${esc(solverId)}</b>.</div>
    </div>
    
    <div style="background:var(--purple-bg); border:1px solid var(--purple-border); padding:12px; border-radius:10px;">
      <div style="font-size:13px; font-weight:800; color:var(--purple); margin-bottom:6px;">Signatures Détectées (${Array.isArray(sigs) ? sigs.length : 1})</div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${(Array.isArray(sigs) ? sigs : [sigs]).map((s, idx) => `
          <div style="background:var(--surface); padding:6px 10px; border-radius:6px; font-size:12.5px; border:1px solid var(--border);">
            <b>#${idx + 1} Action :</b> <span class="badge badge--purple">${esc(s.action || '?')}</span> 
            <b>Objet :</b> <span class="badge badge--primary">${esc(s.object || '?')}</span>
            ${s.desired_state ? `<div style="margin-top:2px; font-size:11.5px; color:var(--text-muted);"><b>État Désiré :</b> ${esc(s.desired_state)}</div>` : ''}
          </div>
        `).join('')}
      </div>
    </div>
  </div>`;

  updateInspector('Signature Extractor', 'Signatures', overview, calls, calls[calls.length - 1] || {});
}

function inspectRetriever(missionId, solverId) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const events = (ep._solver_retrieval && (ep._solver_retrieval[solverId] || ep._solver_retrieval[cleanSid] || ep._solver_retrieval['root_solver'])) || [];
  
  let allMatches = [];
  events.forEach(ev => {
    const mList = ev.results || ev.matches || ev.episodes;
    if (Array.isArray(mList)) {
      allMatches.push(...mList);
    } else if (ev.found_mission_id || ev.result_mission_id || ev.goal || ev.result_goal) {
      allMatches.push({
        mission_id: ev.found_mission_id || ev.result_mission_id,
        goal: ev.goal || ev.result_goal,
        score: ev.score,
        similarity: ev.similarity,
        summary: ev.summary
      });
    }
  });

  // Dédupliquer par mission_id si présent
  const seenMids = new Set();
  const matches = [];
  for (const m of allMatches) {
    const mid = m.mission_id || m.id;
    if (mid && !seenMids.has(mid)) {
      seenMids.add(mid);
      matches.push(m);
    } else if (!mid) {
      matches.push(m);
    }
  }

  const lastEv = events[events.length - 1] || {};

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Retriever (Mémoire Épisodique)</div>
      <div style="font-size:14px; margin-top:4px;">Recherche de cas et missions similaires dans la mémoire pour orienter le Solver <b>${esc(solverId)}</b>.</div>
    </div>
    
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:8px;">Missions Similaires Trouvées (${matches.length})</div>
      ${matches.length === 0 ? '<div style="color:var(--text-faint); font-size:12.5px;">Aucune mission similaire dans l\'index.</div>' : `
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${matches.map((m, mIdx) => {
            const mMid = m.mission_id || m.id;
            const scoreText = typeof m.score === 'number' ? `Sim: ${(m.score * 100).toFixed(1)}%` : (m.similarity || '');
            return `
              <div style="background:var(--surface-alt); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:12.5px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <span style="font-weight:800; font-family:var(--mono); color:var(--primary);">#${mIdx + 1} · ${esc(mMid)}</span>
                  ${scoreText ? `<span class="badge badge--primary">${esc(scoreText)}</span>` : ''}
                </div>
                <div style="margin-top:4px; font-weight:600; color:var(--text);">${esc(m.goal || 'Objectif non précisé')}</div>
                ${m.summary ? `<div style="margin-top:4px; color:var(--text-muted); font-size:12px;">${esc(m.summary)}</div>` : ''}
                <div style="margin-top:8px;">
                  <button class="badge badge--primary" style="cursor:pointer;" onclick="openMission('${esc(mMid)}');">
                    Naviguer vers cette mission ➔
                  </button>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      `}
    </div>
  </div>`;

  updateInspector('Retriever', 'Recherche', overview, [], lastEv);
}

function inspectCompactor(missionId, solverId) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_compactor && (ep._solver_compactor[solverId] || ep._solver_compactor[cleanSid])) || [];
  const call = calls[calls.length - 1];
  const resp = call?.response || {};

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Mission Compactor</div>
      <div style="font-size:14px; margin-top:4px;">Compression sémantique et préparation du contexte pour le Solver <b>${esc(solverId)}</b>.</div>
    </div>
    
    <div style="background:var(--purple-bg); border:1px solid var(--purple-border); padding:12px; border-radius:10px;">
      <div style="font-size:13px; font-weight:800; color:var(--purple); margin-bottom:6px;">Synthèse & Contexte Compressé</div>
      <div style="font-size:12.5px; color:var(--text); line-height:1.5; white-space:pre-wrap;">${esc(resp.summary || resp.compacted_context || fmtJson(resp))}</div>
    </div>
  </div>`;

  updateInspector('Mission Compactor', 'Synthèse', overview, calls, call || {});
}

function inspectFeasibility(missionId, solverId) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_feasibility && (ep._solver_feasibility[solverId] || ep._solver_feasibility[cleanSid])) || [];
  const call = calls[calls.length - 1];
  const resp = call?.response || {};
  const isFeas = resp.is_possible !== false && resp.feasible !== false;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Évaluation de Faisabilité</div>
      <div style="font-size:14px; margin-top:4px;">Analyse cognitive préliminaire de faisabilité pour le Solver <b>${esc(solverId)}</b>.</div>
    </div>
    
    <div style="background:var(--surface-alt); padding:12px; border-radius:10px; border-left:4px solid ${isFeas ? 'var(--success)' : 'var(--failure)'}; border:1px solid var(--border);">
      <div style="font-weight:800; font-size:13.5px;">Statut : ${isFeas ? '✅ FAISABLE' : '❌ REJETÉ / INFAISABLE'}</div>
      ${resp.reason || resp.rationale ? `<div style="font-size:12.5px; margin-top:6px; color:${isFeas ? 'var(--text-muted)' : 'var(--failure)'};"><b>${isFeas ? 'Raisonnement :' : 'Motif de rejet :'}</b> ${esc(resp.reason || resp.rationale)}</div>` : ''}
      ${resp.refined_strategy ? `<div style="margin-top:8px; font-size:12.5px; color:var(--text);"><b>🎯 Stratégie Raffinée :</b> ${esc(resp.refined_strategy)}</div>` : ''}
      ${resp.missing_tools ? `<div style="margin-top:6px; font-size:12px; color:var(--warning);"><b>Outils manquants :</b> ${esc(JSON.stringify(resp.missing_tools))}</div>` : ''}
    </div>
  </div>`;

  updateInspector(isFeas ? 'Faisabilité: Validée' : 'Faisabilité: Rejetée', isFeas ? 'Faisable' : 'Rejeté', overview, calls, call || {});
}

function inspectStep(missionId, solverId, attemptNum, stepId) {
  const ep = findEpisode(missionId);
  if (!ep) return;

  const tree = ep.execution_tree || {};
  let targetNode = null;
  let targetAttempt = null;

  function findStepInTree(node) {
    if (!node) return;
    if (node.solver_id === solverId || (!solverId && node)) {
      const attempts = node.attempts || [];
      const att = attempts[attemptNum] || attempts[0];
      if (att) {
        for (const stp of (att.nodes || [])) {
          if (stp.step_id === stepId) {
            targetNode = stp;
            targetAttempt = att;
            return;
          }
        }
      }
    }
    for (const att of (node.attempts || [])) {
      for (const stp of (att.nodes || [])) {
        if (stp.step_id === stepId) {
          targetNode = stp;
          targetAttempt = att;
          return;
        }
        if (stp.child_execution_tree) {
          findStepInTree(stp.child_execution_tree);
        }
      }
    }
  }

  findStepInTree(tree);

  if (!targetNode) {
    targetNode = { step_id: stepId, description: 'Étape non trouvée', status: 'unknown' };
  }

  let calls = targetNode._node_calls ? [...targetNode._node_calls] : [];
  if (targetNode._tools_manager_llm_calls) {
    calls = calls.concat(targetNode._tools_manager_llm_calls);
  }

  const isFailed = targetNode.status === 'failed' || !!targetNode.error_reason;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Exécution d'Étape [${esc(targetNode.step_type || 'atomic')}]</div>
      <div style="font-family:var(--mono); font-weight:800; color:var(--primary); font-size:15px; margin-top:2px;"># ${esc(targetNode.step_id)}</div>
      <div style="font-size:14px; margin-top:4px; font-weight:600; color:var(--text);">${esc(targetNode.description || '')}</div>
    </div>

    <div style="display:flex; align-items:center; gap:8px;">
      <span style="font-size:12px; font-weight:700; color:var(--text-muted);">Statut :</span>
      ${statusBadge(targetNode.status || 'pending')}
      ${targetNode.tool_name ? `<span class="badge badge--primary">🔧 Outil : ${esc(targetNode.tool_name)}</span>` : ''}
    </div>

    ${targetNode.expected_result ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Résultat Attendu</div>
        <div style="background:var(--surface-alt); border:1px solid var(--border); padding:10px; border-radius:8px; font-size:12.5px; color:var(--text);">${esc(targetNode.expected_result)}</div>
      </div>
    ` : ''}

    ${targetNode.actual_result ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Résultat Obtenu</div>
        <div style="background:var(--surface-alt); border:1px solid var(--border); padding:10px; border-radius:8px; font-size:12.5px; color:var(--text); white-space:pre-wrap;">${esc(typeof targetNode.actual_result === 'object' ? JSON.stringify(targetNode.actual_result, null, 2) : targetNode.actual_result)}</div>
      </div>
    ` : ''}

    ${targetNode.error_reason ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--failure); text-transform:uppercase; margin-bottom:4px;">Erreur Rencontrée</div>
        <div style="background:var(--failure-bg); border:1px solid var(--failure-border); padding:10px; border-radius:8px; font-size:12.5px; color:var(--failure); font-weight:600;">❌ ${esc(targetNode.error_reason)}</div>
      </div>
    ` : ''}

    ${targetNode.tool_args ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Arguments Outil (Inputs)</div>
        <div class="code-box">${esc(JSON.stringify(targetNode.tool_args, null, 2))}</div>
      </div>
    ` : ''}

    ${targetNode._tools_manager_events && targetNode._tools_manager_events.length > 0 ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Appels Internes Tool Manager</div>
        <div style="display:flex; flex-direction:column; gap:6px;">
          ${targetNode._tools_manager_events.map(ev => {
             const evName = ev.event;
             if (evName === 'tools_manager.decision') {
               const statusHtml = statusBadge(ev.decision_success ? 'success' : 'failed');
               const toolHtml = ev.tool_name ? '<div style="margin-bottom:6px;"><b>Outil sélectionné:</b> <span class="badge badge--primary">' + esc(ev.tool_name) + '</span></div>' : '';
               const argsHtml = (ev.tool_args && Object.keys(ev.tool_args).length > 0) ? '<div style="margin-bottom:6px;"><b>Arguments générés:</b> <div class="code-box" style="margin-top:4px;">' + esc(JSON.stringify(ev.tool_args, null, 2)) + '</div></div>' : '';
               const rejectionHtml = ev.rejection_reason ? '<div style="margin-top:6px; color:var(--failure); font-weight:600; background:var(--failure-bg); padding:6px 10px; border-radius:6px; border:1px solid var(--failure-border);">❌ Refus d\'analyse : ' + esc(ev.rejection_reason) + '</div>' : '';
               return '<div style="background:var(--surface-alt); border:1px solid var(--border); padding:10px; border-radius:8px; font-size:12.5px; color:var(--text);">' +
                 '<div style="margin-bottom:6px;"><b>Décision d\'Analyse :</b> ' + statusHtml + '</div>' +
                 toolHtml +
                 argsHtml +
                 rejectionHtml +
               '</div>';
             } else if (evName === 'tools_manager.result') {
               const statusHtml = statusBadge(ev.result ? 'success' : 'failed');
               const errMsg = ev.error_reason || ev.message;
               const msgHtml = errMsg ? '<div style="margin-bottom:6px;"><b>Raison / Message:</b> ' + esc(errMsg) + '</div>' : '';
               const dataHtml = ev.data ? '<div><b>Données:</b> <div class="code-box" style="margin-top:4px;">' + esc(typeof ev.data === 'object' ? JSON.stringify(ev.data, null, 2) : String(ev.data)) + '</div></div>' : '';
               return '<div style="background:var(--surface-alt); border:1px solid var(--border); padding:10px; border-radius:8px; font-size:12.5px; color:var(--text);">' +
                 '<div style="margin-bottom:6px;"><b>Résultat Outil [' + esc(ev.tool_name || '') + '] :</b> ' + statusHtml + '</div>' +
                 msgHtml +
                 dataHtml +
               '</div>';
             }
             return '';
          }).join('')}
        </div>
      </div>
    ` : ''}

    ${targetNode._discovery_sessions && targetNode._discovery_sessions.length > 0 ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Sessions Discovery Rattachées (${targetNode._discovery_sessions.length})</div>
        <div style="display:flex; flex-direction:column; gap:6px;">
          ${targetNode._discovery_sessions.map(ds => `
            <div style="background:var(--purple-bg); border:1px solid var(--purple-border); padding:8px 12px; border-radius:8px; font-size:12px;">
              <b>Data Type :</b> <span class="badge badge--purple">${esc(ds.data_type || '')}</span> · <b>Explorer :</b> ${esc(ds.entity_name || '')}
            </div>
          `).join('')}
        </div>
      </div>
    ` : ''}
  </div>`;

  updateInspector(`Étape: ${targetNode.step_id}`, targetNode.tool_name || 'Step', overview, calls, targetNode);
}

function inspectPlanningCall(missionId, solverId, idx) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_planning && (ep._solver_planning[solverId] || ep._solver_planning[cleanSid])) || [];
  const call = calls[idx];
  const resp = call?.response || {};
  const plan = resp.plan || resp.steps || (Array.isArray(resp) ? resp : []);

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Ingénierie de Plan</div>
      <div style="font-size:14px; margin-top:4px;">Le Planner a traduit la stratégie en étapes formelles HTN pour <b>${esc(solverId)}</b>.</div>
    </div>`;

  if (Array.isArray(plan) && plan.length > 0) {
    overview += `<div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:8px;">Plan Proposé (${plan.length} étapes)</div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${plan.map((st, sIdx) => `
          <div style="background:var(--surface-alt); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:12.5px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-weight:800; font-family:var(--mono); color:var(--primary);">#${sIdx + 1} · ${esc(st.step_id || st.id || ('step_' + (sIdx + 1)))}</span>
              ${st.tool_name ? `<span class="badge badge--primary">🔧 ${esc(st.tool_name)}</span>` : ''}
              <span class="badge badge--purple">${esc(st.step_type || 'atomic')}</span>
            </div>
            <div style="margin-top:4px; font-weight:600; color:var(--text);">${esc(st.description || st.goal || '')}</div>
            ${st.tool_parameters ? `<div style="margin-top:6px;" class="code-box">${esc(JSON.stringify(st.tool_parameters, null, 2))}</div>` : ''}
          </div>
        `).join('')}
      </div>
    </div>`;
  } else if (resp.rationale || resp.plan_summary) {
    overview += `<div style="background:var(--surface-alt); padding:12px; border-radius:8px; border:1px solid var(--border);">
      <b>Synthèse de Plan :</b> ${esc(resp.rationale || resp.plan_summary)}
    </div>`;
  }

  overview += `</div>`;

  updateInspector('Planner LLM', 'Plan', overview, call ? [call] : [], call);
}

function inspectValidationCall(missionId, solverId, idx) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_validation && (ep._solver_validation[solverId] || ep._solver_validation[cleanSid])) || [];
  const call = calls[idx];
  const resp = call?.response || {};
  const isAppr = resp.is_conformant !== false && resp.approved !== false && resp.is_valid !== false;

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Supervision & Juge</div>
      <div style="font-size:14px; margin-top:4px;">Validation formelle du plan par le Superviseur avant toute exécution.</div>
    </div>
    
    <div style="background:var(--surface-alt); padding:12px; border-radius:10px; border-left:4px solid ${isAppr ? 'var(--success)' : 'var(--failure)'}; border:1px solid var(--border);">
      <div style="font-weight:800; font-size:13.5px;">Statut : ${isAppr ? '✅ PLAN VALIDÉ' : '❌ PLAN REJETÉ'}</div>
      ${resp.critique || resp.reason || resp.feedback ? `
        <div style="font-size:12.5px; margin-top:6px; color:${isAppr ? 'var(--text-muted)' : 'var(--failure)'};">
          <b>Critique :</b> ${esc(resp.critique || resp.reason || resp.feedback)}
        </div>
      ` : ''}
      ${resp.missing_preconditions ? `
        <div style="font-size:12px; margin-top:6px; color:var(--warning);">
          <b>Préconditions manquantes :</b> ${esc(JSON.stringify(resp.missing_preconditions))}
        </div>
      ` : ''}
    </div>
  </div>`;

  updateInspector(isAppr ? 'Supervisor: Validé' : 'Supervisor: Rejeté', isAppr ? 'Validé' : 'Rejeté', overview, call ? [call] : [], call);
}

function inspectLearnerCall(missionId, solverId, idx) {
  const ep = findEpisode(missionId);
  const cleanSid = solverId.replace(/^solver_/, '');
  const calls = (ep._solver_learner && (ep._solver_learner[solverId] || ep._solver_learner[cleanSid])) || [];
  const call = calls[idx];
  const resp = call?.response || {};
  const lessons = resp.lessons || resp.extracted_lessons || (Array.isArray(resp) ? resp : [resp]);

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Learner (Extraction de Leçons)</div>
      <div style="font-size:14px; margin-top:4px;">Apprentissage post-exécution pour alimenter la base de leçons stratégiques.</div>
    </div>
    
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:8px;">Leçons Extraites (${lessons.length})</div>
      <div style="display:flex; flex-direction:column; gap:8px;">
        ${lessons.map((l, lIdx) => `
          <div class="lesson-box ${l.polarity === 'prefer' ? 'polarity-prefer' : 'polarity-avoid'}">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <span class="badge badge--purple">${esc(l.entity_type || 'Solver')} · ${esc(l.scope || 'general')}</span>
              <span class="badge ${l.polarity === 'prefer' ? 'badge--success' : 'badge--failed'}">${esc(l.polarity || 'neutral')}</span>
            </div>
            <div style="font-size:13.5px; font-weight:700; color:var(--text); line-height:1.4;">${esc(l.recommendation || l.lesson || fmtJson(l))}</div>
            ${l.keywords && l.keywords.length > 0 ? `
              <div style="margin-top:8px; display:flex; gap:4px; flex-wrap:wrap;">
                ${l.keywords.map(k => `<span style="font-size:11px; background:var(--surface-alt); padding:2px 6px; border-radius:4px; font-family:var(--mono);">${esc(k)}</span>`).join('')}
              </div>
            ` : ''}
          </div>
        `).join('')}
      </div>
    </div>
  </div>`;

  updateInspector('Learner', 'Leçons', overview, call ? [call] : [], call);
}

function inspectPresentator(missionId) {
  const ep = findEpisode(missionId);
  const calls = ep._presentator_calls || [];
  const pres = ep.presentator_result || {};
  const userText = pres.user_response || pres.text || pres.final_text || (calls[calls.length - 1]?.response?.user_response) || '';
  const systemSummary = pres.system_summary || pres.summary || (calls[calls.length - 1]?.response?.system_summary) || '';

  let overview = `<div style="display:flex; flex-direction:column; gap:14px;">
    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase;">Presentator (Restitution Finale)</div>
      <div style="font-size:14px; margin-top:4px;">Génération de la réponse finale à l'utilisateur et synthèse retenue pour l'historique.</div>
    </div>

    <div>
      <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Réponse Livrée à l'Utilisateur</div>
      <div style="background:var(--surface-alt); border:1px solid var(--border); padding:14px; border-radius:10px; font-size:13.5px; line-height:1.5; color:var(--text); white-space:pre-wrap;">${esc(userText || '(Aucune réponse utilisateur enregistrée)')}</div>
    </div>

    ${systemSummary ? `
      <div>
        <div style="font-size:11px; font-weight:700; color:var(--text-faint); text-transform:uppercase; margin-bottom:4px;">Synthèse Système (Mémoire Retenue)</div>
        <div style="background:var(--purple-bg); border:1px solid var(--purple-border); padding:12px; border-radius:10px; font-size:13px; line-height:1.4; color:var(--text); white-space:pre-wrap;">${esc(systemSummary)}</div>
      </div>
    ` : ''}
    
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="font-size:12px; font-weight:700; color:var(--text-muted);">Statut :</span>
      ${statusBadge(pres.status || 'success')}
    </div>
    
    ${pres.error_reason ? `<div style="color:var(--failure); font-weight:600; background:var(--failure-bg); padding:8px 12px; border-radius:8px; margin-top:4px;">❌ ${esc(pres.error_reason)}</div>` : ''}
  </div>`;

  updateInspector('Presentator', 'Rapport', overview, calls, pres);
}

// ==========================================
// VUE LEÇONS
// ==========================================
function renderLessonsView(filter) {
  const container = document.getElementById('lesson-grid-content');
  const bar = document.getElementById('lessons-filter-bar');
  if (DATA.lessons.length === 0) {
    container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-faint);">Aucune leçon enregistrée en base.</div>';
    return;
  }

  const avoidCount = DATA.lessons.filter(l => l.polarity === 'avoid').length;
  const preferCount = DATA.lessons.filter(l => l.polarity === 'prefer').length;
  const total = DATA.lessons.length;

  bar.innerHTML = `
    <button class="badge ${filter==='all'?'badge--primary':'badge--pending'}" style="cursor:pointer;padding:6px 12px;" onclick="renderLessonsView('all')">Toutes (${total})</button>
    <button class="badge ${filter==='avoid'?'badge--failed':'badge--pending'}" style="cursor:pointer;padding:6px 12px;" onclick="renderLessonsView('avoid')">🚫 Avoid (${avoidCount})</button>
    <button class="badge ${filter==='prefer'?'badge--success':'badge--pending'}" style="cursor:pointer;padding:6px 12px;" onclick="renderLessonsView('prefer')">✅ Prefer (${preferCount})</button>
  `;

  const filtered = filter === 'all' ? DATA.lessons : DATA.lessons.filter(l => l.polarity === filter);

  container.innerHTML = filtered.map(l => `
    <div class="lesson-box ${l.polarity === 'prefer' ? 'polarity-prefer' : 'polarity-avoid'}">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span class="badge badge--purple">${esc(l.entity_type)} · ${esc(l.scope)}</span>
        <div style="display:flex; gap:6px;">
          ${l.is_consolidated ? '<span class="badge badge--primary">Consolidée</span>' : ''}
          <span class="badge ${l.polarity==='prefer'?'badge--success':'badge--failed'}">${esc(l.polarity)} (conf: ${(l.confidence||0).toFixed(2)})</span>
        </div>
      </div>
      <div style="font-size:14px; font-weight:700; color:var(--text); line-height:1.4;">${esc(l.recommendation)}</div>
      <div style="margin-top:12px; display:flex; gap:6px; flex-wrap:wrap;">
        ${(l.keywords || []).map(k => `<span style="font-size:11px; background:var(--surface-alt); padding:2px 6px; border-radius:4px; font-family:var(--mono);">${esc(k)}</span>`).join('')}
      </div>
    </div>
  `).join('');
}

// ==========================================
// UTILITY & RESIZER FUNCTIONS
// ==========================================
function copyTextToClipboard(btn, text) {
  if (!navigator.clipboard) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.textContent = '✅ Copié !';
    setTimeout(() => { btn.textContent = '📋 Copier'; }, 1800);
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✅ Copié !';
    setTimeout(() => { btn.textContent = '📋 Copier'; }, 1800);
  }).catch(() => {
    btn.textContent = '❌ Erreur';
  });
}

function toggleUserBubbleText(bubbleId, totalLen) {
  const content = document.getElementById(`ucontent-${bubbleId}`);
  const toggleBtn = document.getElementById(`utoggle-${bubbleId}`);
  if (!content || !toggleBtn) return;
  if (content.classList.contains('collapsed-text')) {
    content.classList.remove('collapsed-text');
    toggleBtn.textContent = '▲ Réduire';
  } else {
    content.classList.add('collapsed-text');
    toggleBtn.textContent = `▼ Afficher tout (${totalLen} caractères)`;
  }
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const resizer = document.getElementById('resizer-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed');
  if (resizer) resizer.style.display = sidebar.classList.contains('collapsed') ? 'none' : 'block';
}

function toggleInspector(forceOpen) {
  const inspector = document.getElementById('inspector-pane');
  const resizer = document.getElementById('resizer-inspector');
  if (!inspector) return;
  
  if (forceOpen === true) {
    inspector.classList.remove('collapsed');
  } else if (forceOpen === false) {
    inspector.classList.add('collapsed');
  } else {
    inspector.classList.toggle('collapsed');
  }
  
  const isCollapsed = inspector.classList.contains('collapsed');
  if (resizer) resizer.style.display = isCollapsed ? 'none' : 'block';
  
  const indicator = document.getElementById('tb-inspector-indicator');
  if (indicator) {
    indicator.textContent = isCollapsed ? 'Masqué' : (Math.round(inspector.offsetWidth || 380) + 'px');
  }
}

function initResizers() {
  const sidebar = document.getElementById('sidebar');
  const inspector = document.getElementById('inspector-pane');
  const resizerSidebar = document.getElementById('resizer-sidebar');
  const resizerInspector = document.getElementById('resizer-inspector');

  // Load stored widths
  const savedSidebarW = localStorage.getItem('managent_sidebar_w');
  const savedInspectorW = localStorage.getItem('managent_inspector_w');
  if (savedSidebarW && sidebar) sidebar.style.width = savedSidebarW + 'px';
  if (savedInspectorW && inspector) inspector.style.width = savedInspectorW + 'px';

  // Auto collapse inspector on narrow screens initially
  if (window.innerWidth < 1100 && inspector) {
    inspector.classList.add('collapsed');
    if (resizerInspector) resizerInspector.style.display = 'none';
    const indicator = document.getElementById('tb-inspector-indicator');
    if (indicator) indicator.textContent = 'Masqué';
  }

  // Resizer Sidebar dragging
  if (resizerSidebar && sidebar) {
    let isDragging = false;
    resizerSidebar.addEventListener('mousedown', (e) => {
      isDragging = true;
      resizerSidebar.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      let newW = e.clientX;
      if (newW < 180) newW = 180;
      if (newW > 480) newW = 480;
      sidebar.style.width = newW + 'px';
      localStorage.setItem('managent_sidebar_w', newW);
    });
    document.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        resizerSidebar.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    });
  }

  // Resizer Inspector dragging
  if (resizerInspector && inspector) {
    let isDragging = false;
    resizerInspector.addEventListener('mousedown', (e) => {
      isDragging = true;
      resizerInspector.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      let newW = window.innerWidth - e.clientX;
      if (newW < 240) newW = 240;
      if (newW > 700) newW = 700;
      inspector.style.width = newW + 'px';
      localStorage.setItem('managent_inspector_w', newW);
      const indicator = document.getElementById('tb-inspector-indicator');
      if (indicator) indicator.textContent = Math.round(newW) + 'px';
    });
    document.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        resizerInspector.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    });
  }
}

// ==========================================
// INIT
// ==========================================
const genDate = DATA.generated_at ? new Date(DATA.generated_at) : new Date();
document.getElementById('gen-timestamp').textContent = 'Généré le ' + genDate.toLocaleDateString('fr-FR') + ' à ' + genDate.toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit'});
initResizers();
renderSidebar();

if (currentMissionId) {
  openMission(currentMissionId, false);
} else if (currentSessionId) {
  renderSessionThread(currentSessionId);
}
</script>
</body>
</html>"""

def render_html(data: Dict[str, Any]) -> str:
    data = {**data, "generated_at": datetime.now().isoformat(timespec="seconds")}
    data["constants"] = {
        "RETRIEVAL_THRESHOLD": RETRIEVAL_THRESHOLD,
        "RETRIEVAL_TOP_K": RETRIEVAL_TOP_K
    }
    json_blob = json.dumps(data, ensure_ascii=False, default=str)
    if not json_blob.endswith("}"):
        raise RuntimeError("JSON tronqué !")
    json_blob = json_blob.replace("</script", "<\\/script")
    return HTML_TEMPLATE.replace("__DATA_JSON__", json_blob)

def main():
    parser = argparse.ArgumentParser(description="Génère le rapport d'observabilité HTML autonome.")
    parser.add_argument("--db", default="memory.db", help="Chemin vers la base SQLite memory.db")
    parser.add_argument("--events", default="observability/events.jsonl", help="Chemin vers events.jsonl")
    parser.add_argument("--out", default="observability_report.html", help="Fichier HTML de sortie")
    parser.add_argument("--session", default=None, help="ID de la session à cibler par défaut")
    parser.add_argument("--mission", default=None, help="ID de la mission à cibler et afficher directement")
    parser.add_argument("--max-sessions", type=int, default=20, help="Nombre max de sessions récentes à charger (0 pour tout charger)")
    parser.add_argument("--session-only", action="store_true", help="N'exporter que les événements de la session demandée")
    args = parser.parse_args()

    data = build_data(
        db_path=args.db,
        events_path=args.events,
        target_session_id=args.session,
        target_mission_id=args.mission,
        max_sessions=args.max_sessions,
        session_only=args.session_only,
    )
    html = render_html(data)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Rapport généré : {args.out}")
    print(f"  {len(data['episodes'])} mission(s), {len(data['lessons'])} leçon(s), "
          f"{len(data['sessions'])} session(s).")

if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
build_observability_report.py (v8.17)
=====================================
- Ajout de la section Discovery (Progressive Disclosure)
- Affichage du plan généré par l'Explorer (LLM call)
- Affichage des étapes, outils, questions/réponses
- Rattachement des découvertes aux entités et missions
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
from core.constants import RETRIEVAL_THRESHOLD, RETRIEVAL_TOP_K

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
            "_solver_preparations": {},
            "_solver_retrieval": {},
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
        cur.execute("SELECT * FROM lessons ORDER BY entity_type ASC, confidence DESC")
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
# TAGS
# =====================================================

PRESENTATOR_TAGS = {"generate_text", "Presentator_report", "Presentator_error", "Presentator_output"}
FEASIBILITY_TAGS = {"FeasibilityDecision", "SignatureExtractor"}
PLANNING_TAGS = {"Plan", "RerankedLessons", "MissionCompactor"}
CONVERGENCE_TAGS = {"ConvergenceDecision"}
EXPLORER_PLAN_TAGS = {"explorer_plan_generation"}

# =====================================================
# UTILITAIRES
# =====================================================

def _parse_ts(ts) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

def _store_call_in_episode(ep, call, tag):
    if tag in FEASIBILITY_TAGS:
        ep.setdefault("_other_calls", []).append(call)
    elif tag in PLANNING_TAGS:
        ep.setdefault("_planning_calls", []).append(call)
    elif tag in PRESENTATOR_TAGS:
        ep.setdefault("_presentator_calls", []).append(call)
    elif tag == "OrchestratorDecision":
        ep.setdefault("_routing_calls", []).append(call)
    else:
        ep.setdefault("_other_calls", []).append(call)

def _store_call_on_attempt(attempt, call, tag):
    if tag in FEASIBILITY_TAGS:
        attempt.setdefault("_feasibility_calls", []).append(call)
    elif tag in PLANNING_TAGS:
        attempt.setdefault("_planning_calls", []).append(call)
    elif tag in CONVERGENCE_TAGS:
        attempt.setdefault("_convergence_calls", []).append(call)
    elif tag in PRESENTATOR_TAGS:
        pass
    elif tag == "OrchestratorDecision":
        pass
    else:
        attempt.setdefault("_other_calls", []).append(call)

def _collect_attempts(tree, attempt_index, all_attempts):
    if not tree:
        return
    solver_id = tree.get("solver_id")
    for attempt in tree.get("attempts", []):
        attempt_num = attempt.get("attempt_number")
        if solver_id is not None and attempt_num is not None:
            attempt_index[(solver_id, attempt_num)] = attempt
        start = attempt.get("started_at")
        end = attempt.get("ended_at")
        all_attempts.append((attempt, start, end))
        for node in attempt.get("nodes", []):
            child_tree = node.get("child_execution_tree")
            if child_tree:
                _collect_attempts(child_tree, attempt_index, all_attempts)

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
                solver_to_mission.setdefault(sid, mission_id)
            for attempt in node.get("attempts", []):
                for step_node in attempt.get("nodes", []):
                    child = step_node.get("child_execution_tree")
                    if child:
                        traverse(child)
        traverse(tree)
    return solver_to_mission

# =====================================================
# NOUVEAU : RATTACHEMENT DES ÉVÉNEMENTS DISCOVERY
# =====================================================

def build_discovery_data(events: List[Dict], llm_calls: List[Dict]) -> Dict[str, Any]:
    """
    Extrait les événements Discovery et les organise par mission.
    Chaque session contient :
      - session_id, entity_id, entity_name, entity_role
      - goal, data_type, target, technical_goal
      - exit_policy, summary (RefinedContext)
      - steps: liste des étapes
      - explorer_plan_call: l'appel LLM qui a généré le plan (si présent)
      - cache_hit: bool
    """
    discovery_events = [e for e in events if e.get("event", "").startswith("discovery.")]
    sessions_by_id = {}

    # Index des llm_calls par session_id
    llm_calls_by_session = {}
    for call in llm_calls:
        tag = call.get("tag", "")
        if tag in EXPLORER_PLAN_TAGS:
            sid = call.get("session_id")
            if sid:
                llm_calls_by_session.setdefault(sid, []).append(call)

    for ev in discovery_events:
        session_id = ev.get("session_id")
        if not session_id:
            continue
        if session_id not in sessions_by_id:
            sessions_by_id[session_id] = {
                "session_id": session_id,
                "entity_id": ev.get("entity_id"),
                "entity_name": ev.get("entity_name"),
                "entity_role": ev.get("entity_role"),
                "mission_id": ev.get("mission_id"),
                "goal": None,
                "data_type": None,
                "target": None,
                "technical_goal": None,
                "exit_policy": None,
                "summary": None,
                "steps": [],
                "cache_hit": False,
                "explorer_plan_calls": llm_calls_by_session.get(session_id, []),
                "start_time": None,
                "end_time": None,
            }
        session = sessions_by_id[session_id]

        if ev.get("event") == "discovery.session_start":
            session["goal"] = ev.get("goal")
            session["data_type"] = ev.get("data_type")
            session["target"] = ev.get("target")
            session["technical_goal"] = ev.get("technical_goal")
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

    # Organiser par mission
    by_mission = {}
    for session_id, session in sessions_by_id.items():
        mission_id = session.get("mission_id")
        if mission_id:
            by_mission.setdefault(mission_id, []).append(session)

    return {"by_mission": by_mission}

def attach_discovery_to_episodes(episodes, discovery_data):
    for ep in episodes:
        mission_id = ep["mission_id"]
        if mission_id in discovery_data["by_mission"]:
            ep["_discovery_sessions"] = discovery_data["by_mission"][mission_id]

# =====================================================
# RATTACHEMENT DES APPELS LLM AUX TENTATIVES
# =====================================================

def attach_llm_calls_by_mission(episodes, llm_calls):
    calls_by_mission = {}
    orphan_calls = []
    for call in llm_calls:
        mid = call.get("mission_id")
        if mid:
            calls_by_mission.setdefault(mid, []).append(call)
        else:
            orphan_calls.append(call)

    ep_index = {ep["mission_id"]: ep for ep in episodes if ep.get("mission_id")}

    for mid, calls in calls_by_mission.items():
        ep = ep_index.get(mid)
        if not ep:
            continue
        tree = ep.get("execution_tree") or {}
        attempt_index = {}
        all_attempts = []
        _collect_attempts(tree, attempt_index, all_attempts)

        for call in calls:
            solver_id = call.get("solver_id")
            attempt_num = call.get("attempt_number")
            tag = call.get("tag")

            if tag in FEASIBILITY_TAGS:
                if solver_id:
                    ep.setdefault("_solver_preparations", {}).setdefault(solver_id, []).append(call)
                else:
                    ep.setdefault("_other_calls", []).append(call)
                continue

            if tag in CONVERGENCE_TAGS:
                if solver_id is not None and attempt_num is not None:
                    attempt = attempt_index.get((solver_id, attempt_num))
                    if attempt:
                        _store_call_on_attempt(attempt, call, tag)
                        continue
                call_ts = _parse_ts(call.get("ts"))
                if call_ts is not None:
                    matched = None
                    for attempt, start, end in all_attempts:
                        if start is None:
                            continue
                        if call_ts < start:
                            continue
                        if end is not None and call_ts > end:
                            continue
                        matched = attempt
                        break
                    if matched:
                        _store_call_on_attempt(matched, call, tag)
                        continue
                ep.setdefault("_other_calls", []).append(call)
                continue

            if tag in PLANNING_TAGS:
                if solver_id is not None and attempt_num is not None:
                    attempt = attempt_index.get((solver_id, attempt_num))
                    if attempt:
                        _store_call_on_attempt(attempt, call, tag)
                        continue
                call_ts = _parse_ts(call.get("ts"))
                if call_ts is not None:
                    matched = None
                    for attempt, start, end in all_attempts:
                        if start is None:
                            continue
                        if call_ts < start:
                            continue
                        if end is not None and call_ts > end:
                            continue
                        matched = attempt
                        break
                    if matched:
                        _store_call_on_attempt(matched, call, tag)
                        continue
                _store_call_in_episode(ep, call, tag)
                continue

            _store_call_in_episode(ep, call, tag)

    if orphan_calls:
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
                    solver_to_mission.setdefault(sid, mission_id)
                for attempt in node.get("attempts", []):
                    for step_node in attempt.get("nodes", []):
                        child = step_node.get("child_execution_tree")
                        if child:
                            traverse(child)
            traverse(tree)

        for call in orphan_calls:
            solver_id = call.get("solver_id")
            mission_id = solver_to_mission.get(solver_id)
            if mission_id:
                ep = ep_index.get(mission_id)
                if ep:
                    ep.setdefault("_other_calls", []).append(call)

# =====================================================
# RATTACHEMENT DES RETRIEVAL AUX SOLVERS
# =====================================================

def attach_retrieval_to_episodes(episodes, retrieval_events):
    ep_index = {ep["mission_id"]: ep for ep in episodes if ep.get("mission_id")}
    for ev in retrieval_events:
        query_mission_id = ev.get("query_mission_id")
        if query_mission_id and query_mission_id in ep_index:
            ep = ep_index[query_mission_id]
            ep.setdefault("_solver_retrieval", {}).setdefault(query_mission_id, []).append(ev)

# =====================================================
# RATTACHEMENT DES ORCHESTRATOR AUX SESSION_TURNS
# =====================================================

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
        for turn in turns_by_session[sid]:
            turn_ts = _parse_ts(turn.get("ts"))
            if turn_ts is not None and turn_ts >= call_ts:
                best = turn
                break
        if best:
            best["_routing_call"] = call

# =====================================================
# PARSER DE SIGNATURES
# =====================================================

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

# =====================================================
# CONSTRUCTION DES DONNÉES
# =====================================================

def build_data(db_path: str, events_path: str) -> Dict[str, Any]:
    episodes = load_episodes(db_path)
    lessons = load_lessons(db_path)
    events = load_events(events_path)

    session_turns = [e for e in events if e.get("event") == "session_turn"]
    llm_calls = [e for e in events if e.get("event") == "llm_call"]
    retrieval_events = [e for e in events if e.get("event") == "retriever_results"]

    attach_llm_calls_by_mission(episodes, llm_calls)
    attach_retrieval_to_episodes(episodes, retrieval_events)

    # Discovery
    discovery_data = build_discovery_data(events, llm_calls)
    attach_discovery_to_episodes(episodes, discovery_data)

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

    sessions = {}
    for turn in session_turns:
        sessions.setdefault(turn.get("session_id", "?"), []).append(turn)
    for turns in sessions.values():
        turns.sort(key=lambda t: t.get("ts") or "", reverse=False)

    return {
        "episodes": episodes,
        "lessons": lessons,
        "sessions": [{"session_id": sid, "turns": turns} for sid, turns in sessions.items()],
        "clock_offset_detected": 0,
    }

# =====================================================
# RENDU HTML
# =====================================================

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

# =====================================================
# GABARIT HTML COMPLET (v8.17)
# =====================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Observabilité — ManAgent</title>
<style>
/* --- styles inchangés --- */
:root {
  --bg: #f6f7f5;
  --surface: #ffffff;
  --surface-alt: #eef1ec;
  --border: #d3d9d0;
  --border-strong: #aab3a5;
  --text: #1c211b;
  --text-muted: #4d5750;
  --text-faint: #7c8579;
  --success: #0f7a3d;
  --success-bg: #e2f5e7;
  --failure: #b3251a;
  --failure-bg: #fbe7e5;
  --skipped: #8a5c00;
  --skipped-bg: #fbf0d9;
  --pending: #4d5770;
  --pending-bg: #e9ecf3;
  --accent: #1857b3;
  --accent-bg: #e4edfb;
  --accent-2: #7a2fb3;
  --accent-2-bg: #f3e8fb;
  --user-bubble: #2ecc71;
  --user-bubble-dark: #27ae60;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --shadow-sm: 0 1px 2px rgba(28,33,27,0.06), 0 1px 1px rgba(28,33,27,0.08);
  --shadow-md: 0 2px 8px rgba(28,33,27,0.10), 0 1px 2px rgba(28,33,27,0.08);
  --shadow-lift: 0 6px 16px rgba(28,33,27,0.14), 0 2px 4px rgba(28,33,27,0.10);
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 17px; line-height: 1.55;
}
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 6px; }
::-webkit-scrollbar-track { background: transparent; }
a { color: var(--accent); }

.app { display: flex; height: 100vh; }

.sidebar {
  width: 320px; flex-shrink: 0; background: var(--surface); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow-y: auto;
}
.sidebar__header { padding: 20px 18px 14px; border-bottom: 1px solid var(--border); }
.sidebar__title { font-size: 19px; font-weight: 800; }
.sidebar__subtitle { font-size: 12px; color: var(--text-faint); margin-top: 3px; font-family: var(--mono); }

.top-nav { display: flex; gap: 6px; padding: 12px; border-bottom: 1px solid var(--border); }
.top-nav__btn {
  flex: 1; padding: 10px 8px; border-radius: 8px; text-align: center; font-size: 13.5px;
  font-weight: 700; cursor: pointer; border: 1.5px solid var(--border); background: var(--surface);
  color: var(--text-muted); transition: all .12s ease;
}
.top-nav__btn:hover { border-color: var(--accent); color: var(--accent); box-shadow: var(--shadow-sm); }
.top-nav__btn.active { background: var(--accent); border-color: var(--accent); color: #fff; box-shadow: var(--shadow-md); }

.session-list { padding: 10px; overflow-y: auto; flex: 1; }
.session-item {
  padding: 13px 14px; border-radius: 10px; cursor: pointer; margin-bottom: 8px;
  background: var(--surface); border: 1.5px solid var(--border); box-shadow: var(--shadow-sm);
  transition: all .12s ease;
}
.session-item:hover { border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }
.session-item.active { border-color: var(--accent); background: var(--accent-bg); box-shadow: var(--shadow-md); }
.session-item__id { font-family: var(--mono); font-size: 12.5px; color: var(--text-muted); }
.session-item__count { font-size: 14.5px; font-weight: 700; margin-top: 3px; }
.session-item__time { font-size: 11.5px; color: var(--text-faint); margin-top: 3px; font-family: var(--mono); }

.main { flex: 1; overflow-y: auto; padding: 32px 40px 80px; }
.view { display: none; }
.view.active { display: block; }
.empty-state {
  color: var(--text-faint); font-size: 16px; padding: 60px 0; text-align: center;
  border: 2px dashed var(--border); border-radius: 14px; background: var(--surface);
}

h2.section-title { font-size: 26px; margin: 0 0 22px; font-weight: 800; }
h3.sub-title { font-size: 15px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin: 22px 0 10px; }

.badge {
  display: inline-block; padding: 4px 11px; border-radius: 999px; font-size: 12.5px;
  font-weight: 800; font-family: var(--mono); letter-spacing: 0.01em; white-space: nowrap;
}
.badge--success { background: var(--success-bg); color: var(--success); }
.badge--failed { background: var(--failure-bg); color: var(--failure); }
.badge--skipped { background: var(--skipped-bg); color: var(--skipped); }
.badge--pending { background: var(--pending-bg); color: var(--pending); }
.badge--entity { background: var(--accent-bg); color: var(--accent); }
.badge--env-real { background: #dbeafe; color: #1e40af; }
.badge--env-simulated { background: #fef3c7; color: #92400e; }
.badge--avoid { background: var(--failure-bg); color: var(--failure); }
.badge--prefer { background: var(--success-bg); color: var(--success); }
.badge--score-high { background: var(--success-bg); color: var(--success); }
.badge--score-medium { background: #fef9e7; color: #b7950b; }
.badge--score-low { background: var(--failure-bg); color: var(--failure); }
.badge--cancelled { background: #fef3c7; color: #92400e; }
.badge--cache { background: #dbeafe; color: #1e40af; }

.lesson-card.polarity-avoid { border-left: 6px solid var(--failure); }
.lesson-card.polarity-prefer { border-left: 6px solid var(--success); }

.filter-btn {
  padding: 6px 16px;
  border-radius: 8px;
  border: 1.5px solid var(--border);
  background: var(--surface);
  font-weight: 700;
  cursor: pointer;
  transition: all .12s ease;
}
.filter-btn:hover { border-color: var(--accent); }
.filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

.dot { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 9px; flex-shrink: 0; }
.dot--success { background: var(--success); }
.dot--failed { background: var(--failure); }
.dot--skipped { background: var(--skipped); }
.dot--pending { background: var(--pending); }

.clickable {
  cursor: pointer; background: var(--surface); border: 1.5px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow-sm); transition: all .12s ease;
}
.clickable:hover { border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }
.clickable:active { transform: translateY(0); box-shadow: var(--shadow-sm); }

.thread-turn { margin-bottom: 22px; }
.thread-turn__user {
  background: var(--user-bubble);
  color: #fff;
  border-radius: 14px 14px 4px 14px;
  padding: 13px 18px;
  max-width: 75%;
  margin-left: auto;
  font-size: 16px;
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}
.thread-turn__meta { font-size: 12px; color: var(--text-faint); font-family: var(--mono); margin: 6px 4px; text-align: right; }
.thread-turn__response {
  background: var(--surface); border-radius: 14px 14px 14px 4px; padding: 16px 20px;
  max-width: 85%; box-shadow: var(--shadow-sm); border: 1.5px solid var(--border); margin-top: 8px;
}
.thread-turn__badge-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.responder-tag { font-family: var(--mono); font-size: 12px; font-weight: 800; color: var(--accent); }

.mission-card {
  padding: 16px 20px;
  margin-top: 8px;
  max-width: 85%;
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
  transition: all .12s ease;
}
.mission-card:hover { border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }
.mission-card__title { font-size: 17px; font-weight: 700; margin-bottom: 8px; }
.mission-card__hint { margin-top: 10px; font-size: 13.5px; color: var(--accent); font-weight: 700; }
.mission-card__hint::after { content: " →"; }

.mission-detail {
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 14px;
  padding: 24px 28px;
  box-shadow: var(--shadow-sm);
}
.back-link {
  display: inline-block;
  margin-bottom: 16px;
  font-weight: 700;
  cursor: pointer;
  color: var(--accent);
}
.back-link:hover { text-decoration: underline; }

.mission-header__goal { font-size: 22px; font-weight: 800; margin-bottom: 8px; }
.mission-header__meta {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--text-faint);
  font-family: var(--mono);
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1.5px solid var(--border);
}

.context-signatures {
  background: var(--surface-alt);
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 20px;
  border: 1.5px solid var(--border);
}
.context-signatures__row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.sig-tag {
  display: inline-block;
  background: var(--accent-bg);
  color: var(--accent);
  padding: 4px 12px;
  border-radius: 6px;
  font-family: var(--mono);
  font-size: 13px;
  border: 1px solid var(--border);
}

.retrieval-section {
  background: var(--surface-alt);
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 20px;
  border-left: 4px solid var(--accent-2);
}
.retrieval-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  gap: 12px;
  flex-wrap: wrap;
}
.retrieval-item:last-child { border-bottom: none; }
.retrieval-item__goal { font-weight: 600; flex: 1; }
.retrieval-item__score { font-family: var(--mono); font-size: 13px; }
.retrieval-item__score-high { color: var(--success); }
.retrieval-item__score-medium { color: #b7950b; }
.retrieval-item__score-low { color: var(--failure); }
.retrieval-item__link { color: var(--accent); font-family: var(--mono); font-size: 12px; cursor: pointer; }

.entity-block {
  border-left: 4px solid var(--border-strong);
  padding: 4px 0 4px 18px;
  margin: 14px 0 14px 6px;
}
.entity-block--solver { border-left-color: #4d5770; }
.entity-block--planner { border-left-color: var(--accent); }
.entity-block--executor { border-left-color: #0f7a3d; }
.entity-block--presentator { border-left-color: var(--accent-2); }
.entity-block--feasibility { border-left-color: #e67e22; }
.entity-block--preparation { border-left-color: #e67e22; }
.entity-block--discovery { border-left-color: var(--accent-2); }
.entity-block--other { border-left-color: #7f8c8d; }
.entity-block__label {
  font-family: var(--mono);
  font-size: 12.5px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 6px;
}

details.attempt, details.step, details.llm-call, details.plan-detail, details.discovery-session {
  border: 1.5px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  margin: 8px 0;
  box-shadow: var(--shadow-sm);
  transition: box-shadow .12s ease;
}
details.attempt:hover, details.step:hover, details.llm-call:hover, details.discovery-session:hover {
  box-shadow: var(--shadow-md);
}
details.attempt > summary, details.step > summary, details.llm-call > summary,
details.plan-detail > summary, details.discovery-session > summary {
  padding: 12px 16px;
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}
details > summary::-webkit-details-marker { display: none; }
.chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--accent-bg);
  color: var(--accent);
  font-size: 12px;
  font-weight: 900;
  flex-shrink: 0;
  transition: transform .12s ease;
}
details[open] > summary .chevron { transform: rotate(90deg); }
.attempt-body, .step-body, .llm-call-body, .plan-detail-body, .discovery-body {
  padding: 6px 18px 16px 46px;
  font-size: 15px;
  color: var(--text-muted);
  border-top: 1.5px solid var(--border);
  margin-top: 2px;
  padding-top: 14px;
}
.step-title { font-weight: 700; color: var(--text); }
.step-tool {
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--accent);
  background: var(--accent-bg);
  padding: 2px 8px;
  border-radius: 6px;
}
.raw-tolerated {
  display: inline-block;
  margin-top: 8px;
  padding: 6px 11px;
  border-radius: 8px;
  background: var(--skipped-bg);
  color: var(--skipped);
  font-size: 12.5px;
  font-family: var(--mono);
  font-weight: 700;
}
.advice-box {
  margin-top: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--accent-2-bg);
  border: 1.5px solid #dcc2f2;
  font-size: 14px;
  white-space: pre-wrap;
  color: #5a1f8a;
}
.advice-box__label {
  font-family: var(--mono);
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #5a1f8a;
  margin-bottom: 5px;
  font-weight: 800;
}
.child-tree-wrap {
  margin-top: 10px;
  padding-left: 16px;
  border-left: 3px dashed var(--border-strong);
}
.prompt-block {
  background: var(--surface-alt);
  border: 1.5px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  font-family: var(--mono);
  font-size: 13.5px;
  white-space: pre-wrap;
  max-height: 360px;
  overflow-y: auto;
  margin-top: 8px;
  color: var(--text);
}
.field-label {
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-faint);
  margin-top: 14px;
  font-weight: 800;
}
.llm-call__tag { font-weight: 800; font-family: var(--mono); }
.llm-call__duration { font-family: var(--mono); font-size: 13px; color: var(--text-faint); margin-left: auto; }

.lesson-card { padding: 16px 20px; margin-bottom: 12px; }
.lesson-card__head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.lesson-card__scope { font-family: var(--mono); font-weight: 800; font-size: 15.5px; }
.lesson-card__stats { font-family: var(--mono); font-size: 12.5px; color: var(--text-faint); white-space: nowrap; }
.lesson-card__reco { margin-top: 8px; font-size: 16px; }
.kw-tag {
  display: inline-block;
  background: var(--surface-alt);
  color: var(--text-muted);
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 6px;
  margin: 8px 6px 0 0;
  font-family: var(--mono);
}
.source-ep-link {
  display: inline-block;
  margin: 8px 6px 0 0;
  padding: 4px 10px;
  border-radius: 7px;
  background: var(--accent-bg);
  color: var(--accent);
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  border: 1.5px solid transparent;
}
.source-ep-link:hover { border-color: var(--accent); box-shadow: var(--shadow-sm); }
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="sidebar__header">
      <div class="sidebar__title">Observabilité - ManAgent</div>
      <div class="sidebar__subtitle" id="generated-at"></div>
    </div>
    <div class="top-nav">
      <div class="top-nav__btn active" data-nav="sessions">Sessions</div>
      <div class="top-nav__btn" data-nav="lessons">Leçons</div>
    </div>
    <div class="session-list" id="session-list"></div>
  </div>
  <div class="main">
    <div class="view" id="view-sessions"></div>
    <div class="view" id="view-lessons"></div>
  </div>
</div>

<script id="data-island" type="application/json">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById('data-island').textContent);

// Récupération des constantes
const THRESHOLD = DATA.constants ? DATA.constants.RETRIEVAL_THRESHOLD : 0.85;
const TOP_K = DATA.constants ? DATA.constants.RETRIEVAL_TOP_K : 20;

console.log("[Observabilité] Épisodes chargés :", DATA.episodes.length);
console.log("[Observabilité] Sessions chargées :", DATA.sessions.length);

let currentNav = 'sessions';
let currentSessionId = DATA.sessions[0] ? DATA.sessions[0].session_id : null;
let currentMissionId = null;

// =====================================================
// FONCTIONS DE FORMATAGE
// =====================================================

function formatTimestamp(ts) {
  if (!ts) return '—';
  let d;
  if (typeof ts === 'number' || (typeof ts === 'string' && !isNaN(parseFloat(ts)) && isFinite(ts))) {
    d = new Date(parseFloat(ts) * 1000);
  } else {
    d = new Date(ts);
  }
  if (isNaN(d.getTime())) return String(ts);
  const now = new Date();
  const isToday = d.getFullYear() === now.getFullYear() &&
                  d.getMonth() === now.getMonth() &&
                  d.getDate() === now.getDate();
  if (isToday) {
    return d.toTimeString().slice(0, 8);
  } else {
    return d.toLocaleDateString('fr-FR') + ' ' + d.toTimeString().slice(0, 5);
  }
}

function formatDuration(ms) {
  if (!ms || ms < 0) return '—';
  if (ms < 1000) return Math.round(ms) + 'ms';
  const sec = ms / 1000;
  if (sec < 60) return sec.toFixed(1) + 's';
  const min = Math.floor(sec / 60);
  const reste = Math.round(sec % 60);
  return min + 'm ' + reste + 's';
}

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function fmtJson(obj) {
  if (obj === null || obj === undefined) return '';
  if (typeof obj === 'string') return obj;
  try { return JSON.stringify(obj, null, 2); } catch(e) { return String(obj); }
}
function statusBadge(status) {
  const cls = {success: 'success', failed: 'failed', skipped: 'skipped', pending: 'pending', cancelled: 'cancelled'}[status] || 'pending';
  return `<span class="badge badge--${cls}">${esc(status || '?')}</span>`;
}
function envBadge(env) {
  const cls = env === 'real' ? 'env-real' : 'env-simulated';
  return `<span class="badge badge--${cls}">${esc(env || 'simulated')}</span>`;
}
function findEpisode(missionId) {
    return DATA.episodes.find(e => String(e.mission_id).trim() === String(missionId).trim());
}

// =====================================================
// APPELS LLM (rendu)
// =====================================================
function renderLlmCall(c) {
  const ok = c.success !== false;
  const durationStr = c.duration_ms ? formatDuration(c.duration_ms) : '';
  const tag = (c.tag || c.schema || '').trim();
  return `<details class="llm-call">
    <summary>
      <span class="chevron">▸</span>
      <span class="llm-call__tag" style="color:${ok ? 'var(--accent)' : 'var(--failure)'}">${esc(tag || '?')}</span>
      ${statusBadge(ok ? 'success' : 'failed')}
      <span class="llm-call__duration">${durationStr}</span>
    </summary>
    <div class="llm-call-body">
      ${!ok ? `<div style="color:var(--failure);font-weight:700">${esc(c.error_type||'Erreur')} : ${esc(c.error||'(message vide)')}</div>` : ''}
      <div class="field-label">Prompt</div>
      <div class="prompt-block">${esc(c.prompt || '')}</div>
      ${c.context && c.context.length ? `<div class="field-label">Contexte</div><div class="prompt-block">${esc(fmtJson(c.context))}</div>` : ''}
      ${c.response !== undefined ? `<div class="field-label">Réponse</div><div class="prompt-block">${esc(fmtJson(c.response))}</div>` : ''}
    </div>
  </details>`;
}
function renderLlmCalls(calls) {
  if (!calls || calls.length === 0) return '';
  return calls.map(renderLlmCall).join('');
}

// =====================================================
// RENDU DISCOVERY (Progressive Disclosure)
// =====================================================
function renderDiscoverySessions(ep) {
  const sessions = ep._discovery_sessions || [];
  if (!sessions || sessions.length === 0) return '';

  let html = `<div style="margin-top: 24px; border-top: 2px solid var(--border); padding-top: 16px;">
    <h3 style="font-size: 18px; font-weight: 800; color: var(--accent-2); margin: 0 0 12px;">🔍 Découvertes (Progressive Disclosure)</h3>`;

  sessions.forEach((s, idx) => {
    const steps = s.steps || [];
    const calls = s.explorer_plan_calls || [];

    html += `<details class="discovery-session" ${idx === 0 ? 'open' : ''}>
      <summary>
        <span class="chevron">▸</span>
        <span style="font-weight:700;">🧠 ${esc(s.entity_name || '?')} (${esc(s.entity_role || '?')})</span>
        <span class="badge badge--entity">${esc(s.goal || '?')}</span>
        <span class="badge badge--info" style="background:var(--accent-bg);color:var(--accent);">${esc(s.data_type || '?')} / ${esc(s.target || '?')}</span>
        ${s.cache_hit ? '<span class="badge badge--cache">⚡ Cache</span>' : ''}
        ${s.exit_policy ? `<span class="badge badge--${s.exit_policy === 'plan_completed' ? 'success' : 'failed'}">${esc(s.exit_policy)}</span>` : ''}
        <span style="font-size:13px;color:var(--text-faint);font-family:var(--mono);margin-left:auto;">${steps.length} étapes</span>
      </summary>
      <div class="discovery-body">
        <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:14px;margin-bottom:12px;">
          <div style="font-weight:600;color:var(--text-faint);">Goal technique</div>
          <div style="font-family:var(--mono);">${esc(s.technical_goal || '—')}</div>
          <div style="font-weight:600;color:var(--text-faint);">Data type</div>
          <div>${esc(s.data_type || '—')}</div>
          <div style="font-weight:600;color:var(--text-faint);">Target</div>
          <div style="font-family:var(--mono);">${esc(s.target || '—')}</div>
          <div style="font-weight:600;color:var(--text-faint);">Exit policy</div>
          <div><span class="badge badge--${s.exit_policy === 'plan_completed' ? 'success' : 'failed'}">${esc(s.exit_policy || '—')}</span></div>
        </div>

        <!-- Plan généré par l'Explorer (LLM call) -->
        ${calls.length ? `<div class="field-label">📐 Plan généré par l'Explorer</div>${renderLlmCalls(calls)}` : ''}

        <!-- Étapes -->
        ${steps.length ? `<div class="field-label" style="margin-top:12px;">🔄 Étapes exécutées</div>` : ''}
        <div style="padding-left:8px;">
        ${steps.map((step, i) => {
          const result = step.result || {};
          const success = result.success !== false;
          let detailHtml = '';
          if (step.step_type === 'tool') {
            detailHtml = `<span class="step-tool">🔧 ${esc(step.tool_name || '?')}</span>
                          <div style="font-size:13px;color:var(--text-muted);margin-top:4px;">
                            <span style="font-weight:600;">Résultat :</span>
                            ${success ? '<span style="color:var(--success);">✅ succès</span>' : '<span style="color:var(--failure);">❌ échec</span>'}
                            ${result.data ? `<div style="font-family:var(--mono);font-size:12px;background:var(--surface-alt);padding:4px 8px;border-radius:4px;margin-top:4px;">${esc(typeof result.data === 'string' ? result.data : JSON.stringify(result.data))}</div>` : ''}
                          </div>`;
          } else if (step.step_type === 'semantic') {
            detailHtml = `<div style="font-size:13px;color:var(--text-muted);"><span style="font-weight:600;">Question :</span> ${esc(step.question || '?')}</div>
                          <div style="font-size:13px;color:var(--text-muted);"><span style="font-weight:600;">Réponse :</span> ${success ? esc(result.data || '—') : '<span style="color:var(--failure);">❌ échec</span>'}</div>`;
          }
          return `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--surface-alt);align-items:flex-start;">
            <span style="font-family:var(--mono);font-size:12px;color:var(--text-faint);min-width:60px;">#${i+1}</span>
            <div style="flex:1;">
              <div style="font-weight:600;">${esc(step.description || '?')}</div>
              ${detailHtml}
            </div>
            <span class="badge badge--${success ? 'success' : 'failed'}">${success ? 'OK' : 'KO'}</span>
          </div>`;
        }).join('')}
        </div>

        <!-- RefinedContext final -->
        ${s.summary ? `<div style="margin-top:14px;padding:12px 16px;background:var(--accent-bg);border-radius:8px;border-left:4px solid var(--accent);">
          <div style="font-weight:700;font-size:14px;color:var(--text);">📝 RefinedContext (connaissance acquise)</div>
          <div style="white-space:pre-wrap;font-size:14px;color:var(--text-muted);margin-top:4px;">${esc(s.summary)}</div>
        </div>` : ''}
      </div>
    </details>`;
  });

  html += `</div>`;
  return html;
}

// =====================================================
// RENDU RETRIEVAL
// =====================================================
function renderRetrievalSection(retrievals) {
  if (!retrievals || retrievals.length === 0) return '';
  let html = `<div class="retrieval-section">
    <h3 style="margin:0 0 12px;font-size:16px;font-weight:800;color:var(--accent-2);">🔍 Retrieval — Missions similaires trouvées</h3>
    <div style="font-size:13px;color:var(--text-faint);margin-bottom:8px;font-family:var(--mono);">
      Top‑K : ${TOP_K} · Seuil : ${THRESHOLD.toFixed(2)}
    </div>`;

  retrievals.forEach(r => {
    const score = r.score || 0;
    let scoreClass = 'score-low';
    let scoreLabel = 'Faible';
    if (score >= THRESHOLD) {
      scoreClass = 'score-high';
      scoreLabel = 'Élevée';
    } else if (score >= THRESHOLD - 0.15) {
      scoreClass = 'score-medium';
      scoreLabel = 'Moyenne';
    }

    const foundId = r.found_mission_id || r.mission_id;
    const summary = r.summary || r.goal || 'Mission sans résumé';

    html += `<div class="retrieval-item">
      <span class="retrieval-item__goal">${esc(summary)}</span>
      <span class="retrieval-item__score retrieval-item__${scoreClass}">${score.toFixed(3)} (${scoreLabel})</span>
      <span class="retrieval-item__link" onclick="openMission('${esc(foundId)}')">↳ voir</span>
    </div>`;
  });

  html += `</div>`;
  return html;
}

// =====================================================
// RENDU RECURSIF DE L'ARBRE DES SOLVERS
// =====================================================

function renderSolverTree(ep, tree) {
    const solverId = tree.solver_id;
    if (!solverId) return '';

    const prepCalls = (ep._solver_preparations && ep._solver_preparations[solverId]) || [];
    const retrievalResults = (ep._solver_retrieval && ep._solver_retrieval[solverId]) || [];

    let extractedSignatures = [];
    prepCalls.forEach(c => {
        if (c.tag === 'SignatureExtractor' && c.response && c.response.signatures) {
            extractedSignatures = c.response.signatures;
        }
    });

    const goal = tree.goal || 'Objectif non défini';
    const shortGoal = goal.length > 80 ? goal.substring(0, 80) + '…' : goal;

    let html = `<div style="border-left: 4px solid #4d5770; padding-left: 12px; margin: 16px 0; border-radius: 4px;">`;
    html += `<div style="font-size:15px; font-weight:700; color:var(--text); margin-bottom:6px;">
      🧠 Solver [${esc(solverId)}] — ${esc(shortGoal)}
      <span style="font-weight:400; font-size:13px; color:var(--text-faint); margin-left:10px;">
        ${statusBadge(tree.status)}
      </span>
    </div>`;

    const sigCalls = prepCalls.filter(c => c.tag === 'SignatureExtractor');
    if (sigCalls.length) {
        html += `<div class="entity-block entity-block--preparation">
          <div class="entity-block__label">📝 Extraction de signatures</div>`;
        if (extractedSignatures.length) {
            html += `<div style="margin-bottom:8px; display:flex; flex-wrap:wrap; gap:6px;">`;
            extractedSignatures.forEach(s => {
                let label = `${s.action} ${s.object}`;
                if (s.desired_state) label += ` → ${s.desired_state}`;
                html += `<span class="sig-tag">${esc(label)}</span>`;
            });
            html += `</div>`;
        }
        html += renderLlmCalls(sigCalls);
        html += `</div>`;
    }

    if (retrievalResults.length) {
        html += renderRetrievalSection(retrievalResults);
    }

    const feasCalls = prepCalls.filter(c => c.tag === 'FeasibilityDecision');
    if (feasCalls.length) {
        html += `<div class="entity-block entity-block--feasibility">
          <div class="entity-block__label">📋 Évaluation de la faisabilité</div>`;
        for (const call of feasCalls) {
            const response = call.response || {};
            const isPossible = response.is_possible;
            const reason = response.reason || '';
            const refinedStrategy = response.refined_strategy || '';

            html += `<div style="margin-bottom:12px;padding:8px 12px;background:var(--surface-alt);border-radius:8px;border-left:3px solid ${isPossible ? 'var(--success)' : 'var(--failure)'};">`;
            html += `<div style="font-weight:700;">${isPossible ? '✅ Faisable' : '❌ Non faisable'}</div>`;
            if (reason) {
                html += `<div style="font-size:14px;color:var(--text-muted);margin-top:4px;">${esc(reason)}</div>`;
            }
            if (refinedStrategy) {
                html += `<div style="margin-top:8px;padding:8px 12px;background:var(--accent-bg);border-radius:6px;border-left:3px solid var(--accent);">`;
                html += `<div style="font-weight:700;font-size:13px;color:var(--accent);">🎯 Stratégie raffinée</div>`;
                html += `<div style="font-size:14px;white-space:pre-wrap;color:var(--text);">${esc(refinedStrategy)}</div>`;
                html += `</div>`;
            }
            html += `</div>`;
        }
        html += renderLlmCalls(feasCalls);
        html += `</div>`;
    }

    const attempts = (tree.attempts || []).map((a, i) => renderAttempt(a, i, ep)).join('');
    html += attempts;

    html += `</div>`;
    return html;
}

// =====================================================
// RENDU DES TENTATIVES ET NŒUDS
// =====================================================

function renderNode(node, ep) {
  const statusCls = node.status || 'pending';
  let toleratedBadge = '';
  if (node.raw_tool_success === false && node.status === 'success') {
    toleratedBadge = `<div class="raw-tolerated">⚠ outil brut = false, toléré (expected_result="any")</div>`;
  } else if (node.raw_tool_success === true && node.status === 'failed') {
    toleratedBadge = `<div class="raw-tolerated">ℹ outil brut = true, mais convergence jugée non satisfaite</div>`;
  }
  const nodeCalls = node._node_calls || [];

  let convCalls = [];
  const allAttempts = [];
  function collectAttempts(tree) {
    if (!tree) return;
    for (let attempt of (tree.attempts || [])) {
      allAttempts.push(attempt);
      for (let n of (attempt.nodes || [])) {
        if (n.child_execution_tree) collectAttempts(n.child_execution_tree);
      }
    }
  }
  collectAttempts(ep.execution_tree);
  for (let attempt of allAttempts) {
    if (attempt._convergence_calls) {
      for (let call of attempt._convergence_calls) {
        if (call.step_id === node.step_id) {
          convCalls.push(call);
        }
      }
    }
  }
  if (convCalls.length === 0) {
    const otherCalls = ep._other_calls || [];
    for (let call of otherCalls) {
      if (call.tag === 'ConvergenceDecision' && call.step_id === node.step_id) {
        convCalls.push(call);
      }
    }
  }

  const seen = new Set();
  const uniqueConv = convCalls.filter(c => {
    const key = c.ts + c.tag + (c.step_id || '');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  let convergenceHtml = '';
  if (uniqueConv.length > 0) {
    convergenceHtml = `<div style="margin-top:12px; padding-left:8px; border-left: 3px solid var(--accent-2);">
        <div style="font-size:13px; font-weight:700; color:var(--text-muted); margin-bottom:4px;">✅ Vérification de convergence</div>
        ${renderLlmCalls(uniqueConv)}
    </div>`;
  }

  let childHtml = '';
  if (node.child_execution_tree) {
      childHtml = `<div style="margin-top:12px; border-top: 1px dashed var(--border-strong); padding-top:12px;">
          ${renderSolverTree(ep, node.child_execution_tree)}
      </div>`;
  }

  return `<details class="step">
    <summary>
      <span class="chevron">▸</span>
      <span class="dot dot--${statusCls}"></span>
      <span class="step-title">${esc(node.step_id)}</span>
      ${node.tool_name ? `<span class="step-tool">${esc(node.tool_name)}</span>` : ''}
      <span style="color:var(--text-faint);font-size:13px">${esc(node.step_type)}</span>
      ${statusBadge(node.status)}
    </summary>
    <div class="step-body">
      <div>${esc(node.description)}</div>
      ${node.expected_result ? `<div style="margin-top:8px"><b>attendu:</b> ${esc(node.expected_result)}</div>` : ''}
      ${node.actual_result ? `<div><b>réel:</b> ${esc(node.actual_result)}</div>` : ''}
      ${node.error_reason ? `<div style="color:var(--failure);margin-top:8px;font-weight:600">${esc(node.error_reason)}</div>` : ''}
      ${toleratedBadge}
      ${nodeCalls.length ? `<div class="field-label">Vérification de convergence (Executor)</div>${renderLlmCalls(nodeCalls)}` : ''}
      ${convergenceHtml}
      ${childHtml}
    </div>
  </details>`;
}

function renderAttempt(attempt, idx, ep) {
  let adviceHtml = '';
  if (attempt.advice_injected) {
    adviceHtml = `<div class="advice-box"><div class="advice-box__label">Conseil injecté dans ce plan</div>${esc(attempt.advice_injected)}</div>`;
  }
  const entityBadge = attempt.target_entity ? `<span class="badge badge--entity">${esc(attempt.target_entity)}</span>` : '';
  const fcBadge = (attempt.failure_class && attempt.failure_class !== 'none') ? `<span class="badge badge--failed">${esc(attempt.failure_class)}</span>` : '';

  const nodesHtml = (attempt.nodes || []).map(n => renderNode(n, ep)).join('');

  const planningCalls = attempt._planning_calls || [];
  const feasibilityCalls = attempt._feasibility_calls || [];

  let planDetailHtml = '';
  if (attempt.proposed_plan) {
    const planStr = fmtJson(attempt.proposed_plan);
    planDetailHtml = `<details class="plan-detail">
      <summary><span class="chevron">▸</span> 📋 Plan proposé (${attempt.proposed_plan.steps ? attempt.proposed_plan.steps.length : '0'} étapes)</summary>
      <div class="plan-detail-body"><div class="prompt-block">${esc(planStr)}</div></div>
    </details>`;
  }

  return `<details class="attempt" ${idx === 0 ? 'open' : ''}>
    <summary>
      <span class="chevron">▸</span>
      <span class="dot dot--${attempt.outcome === 'success' ? 'success' : 'failed'}"></span>
      <b>Tentative #${attempt.attempt_number}</b>
      ${statusBadge(attempt.outcome)} ${entityBadge} ${fcBadge}
    </summary>
    <div class="attempt-body">
      ${attempt.failure_reason ? `<div style="color:var(--failure);font-weight:600">${esc(attempt.failure_reason)}</div>` : ''}
      <div class="entity-block entity-block--planner">
        <div class="entity-block__label">📐 Planner — construction du plan</div>
        ${planDetailHtml}
        ${planningCalls.length ? renderLlmCalls(planningCalls) : '<div style="color:var(--text-faint);font-size:13.5px">Aucun appel capturé pour cette tentative.</div>'}
        ${adviceHtml}
      </div>
      <div class="entity-block entity-block--executor">
        <div class="entity-block__label">⚙️ Executor — exécution étape par étape</div>
        ${nodesHtml}
      </div>
    </div>
  </details>`;
}

// =====================================================
// RENDU DES MISSIONS (détail)
// =====================================================
function renderMissionDetail(missionId) {
  const ep = findEpisode(missionId);
  if (!ep) return '<div class="empty-state">Mission introuvable.</div>';
  const created = formatTimestamp(ep.created_at);
  const finished = ep.finished_at ? formatTimestamp(ep.finished_at) : '—';
  const analyzed = ep.analyzed_at ? formatTimestamp(ep.analyzed_at) : 'pas encore analysée';

  let html = `<div class="back-link" onclick="backToSession()">← Retour au fil de la session</div>`;
  html += `<div class="mission-detail">
    <div class="mission-header__goal">${esc(ep.goal)}</div>
    <div class="mission-header__meta">
      ${statusBadge(ep.status)} ${envBadge(ep.environment)}
      <span>créée : ${created}</span>
      <span>finie : ${finished}</span>
      <span>${analyzed}</span>
    </div>`;

  const signatures = ep.signatures || [];
  if (signatures.length > 0) {
    html += `<div class="context-signatures">
      <h3 style="margin:0 0 8px;font-size:15px;font-weight:800;color:var(--text-muted);">🎯 Signatures globales</h3>
      <div class="context-signatures__row">`;
    signatures.forEach(s => {
      const action = s.action || 'N/A';
      const object = s.object || 'N/A';
      let label = `${action} ${object}`;
      if (s.desired_state && s.desired_state !== 'None') label += ` → ${s.desired_state}`;
      html += `<span class="sig-tag">${esc(label)}</span>`;
    });
    html += `</div></div>`;
  }

  const tree = ep.execution_tree;
  if (tree && tree.solver_id) {
    html += renderSolverTree(ep, tree);
  } else {
    html += '<div class="empty-state">Aucun arbre d\'exécution disponible.</div>';
  }

  // --- SECTION DISCOVERY ---
  html += renderDiscoverySessions(ep);

  // Presentator
  const presCalls = ep._presentator_calls || [];
  html += `<div class="entity-block entity-block--presentator">
    <div class="entity-block__label">🗣️ Presentator — rédaction du rapport final</div>
    ${ep.presentator_result ? `<div style="margin-bottom:8px">${statusBadge(ep.presentator_result.status)} ${ep.presentator_result.status === 'failed' ? esc(ep.presentator_result.error_reason) : ''}</div>` : ''}
    ${presCalls.length ? renderLlmCalls(presCalls) : '<div style="color:var(--text-faint);font-size:13.5px">Aucun appel capturé.</div>'}
  </div>`;

  const otherCalls = ep._other_calls || [];
  if (otherCalls.length) {
    html += `<div class="entity-block entity-block--other">
      <div class="entity-block__label">⚠️ Appels non rattachés (debug)</div>
      ${renderLlmCalls(otherCalls)}
    </div>`;
  }

  if (ep.resolved_data && Object.keys(ep.resolved_data).length > 0) {
    html += `<div class="field-label">Registre de variables résolu</div><div class="prompt-block">${esc(fmtJson(ep.resolved_data))}</div>`;
  }
  html += `</div>`;
  return html;
}

// =====================================================
// SESSION (fil de discussion en bulles)
// =====================================================
function renderSessionThread(sessionId) {
  const session = DATA.sessions.find(s => s.session_id === sessionId);
  if (!session) return '<div class="empty-state">Aucune session sélectionnée.</div>';

  let html = `<h2 class="section-title">Session <span style="font-family:var(--mono);font-size:18px;color:var(--text-faint)">${esc(sessionId)}</span></h2>`;

  const turns = session.turns.slice().sort((a,b) => (a.ts || 0) - (b.ts || 0));

  turns.forEach(t => {
    const tsStr = formatTimestamp(t.ts);
    html += `<div class="thread-turn">`;
    html += `<div class="thread-turn__user">${esc(t.user_message || '')}</div>`;
    html += `<div class="thread-turn__meta">${tsStr}</div>`;

    if (t.mode === 'direct') {
      html += `<div class="thread-turn__response">
        <div class="thread-turn__badge-row"><span class="responder-tag">🧭 ORCHESTRATOR · réponse directe</span></div>
        ${esc(t.response || '')}
      </div>`;
    } else {
      const ep = findEpisode(t.mission_id);
      html += `<div class="mission-card clickable" onclick="openMission('${esc(t.mission_id)}')">
        <div class="thread-turn__badge-row">
          <span class="responder-tag">🚀 MISSION</span>
          ${ep ? statusBadge(ep.status) : statusBadge('pending')}
          ${t.signatures && t.signatures.length ? `<span style="font-size:12px;color:var(--text-faint);font-family:var(--mono);">signatures : ${t.signatures.map(s => esc(s.action+' '+s.object)).join(', ')}</span>` : ''}
        </div>
        <div class="mission-card__title">${esc(t.refined_goal || (ep && ep.goal) || '')}</div>
        <div class="mission-card__hint">Voir le déroulé complet (Planner, Executor, Presentator)</div>
      </div>`;

      if (t._routing_call) {
        html += `<div style="max-width:85%;margin-top:6px;margin-left:0;border-left:2px solid var(--accent);padding-left:12px;">`;
        html += `<div style="font-size:12px;color:var(--text-faint);font-weight:600;margin-bottom:2px;">🧭 Orchestrator — décision de routage</div>`;
        html += renderLlmCall(t._routing_call);
        html += `</div>`;
      }
    }
    html += `</div>`;
  });

  document.getElementById('view-sessions').innerHTML = html;
}

function openMission(missionId) {
  currentMissionId = missionId;
  document.getElementById('view-sessions').innerHTML = renderMissionDetail(missionId);
}
function backToSession() {
  currentMissionId = null;
  renderSessionThread(currentSessionId);
}

// =====================================================
// LEÇONS
// =====================================================
function renderLessonsView(filter) {
  if (typeof filter === 'undefined') filter = 'all';
  const el = document.getElementById('view-lessons');
  if (DATA.lessons.length === 0) {
    el.innerHTML = '<h2 class="section-title">Leçons</h2><div class="empty-state">Aucune leçon en base.</div>';
    return;
  }
  const avoidCount = DATA.lessons.filter(l => l.polarity === 'avoid').length;
  const preferCount = DATA.lessons.filter(l => l.polarity === 'prefer').length;
  const total = DATA.lessons.length;
  let html = `<h2 class="section-title">Base de leçons (${total})</h2>
    <div style="display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap;">
      <button class="filter-btn ${filter === 'all' ? 'active' : ''}" data-filter="all">Toutes (${total})</button>
      <button class="filter-btn ${filter === 'avoid' ? 'active' : ''}" data-filter="avoid">🚫 Avoid (${avoidCount})</button>
      <button class="filter-btn ${filter === 'prefer' ? 'active' : ''}" data-filter="prefer">✅ Prefer (${preferCount})</button>
    </div>
    <div id="lesson-list">`;
  const filtered = filter === 'all' ? DATA.lessons : DATA.lessons.filter(l => l.polarity === filter);
  filtered.forEach(l => {
    const kws = (l.keywords || []).map(k => `<span class="kw-tag">${esc(k)}</span>`).join('');
    const sources = (l.source_episodes || []).map(mid => {
      const ep = findEpisode(mid);
      const label = ep ? ep.goal : mid;
      return `<span class="source-ep-link" onclick="goToMissionFromLessons('${esc(mid)}')">↳ ${esc((label||'').slice(0,40))}</span>`;
    }).join('');
    const polarityBadge = l.polarity === 'prefer'
      ? `<span class="badge badge--prefer">✅ prefer</span>`
      : `<span class="badge badge--avoid">🚫 avoid</span>`;
    const polarityClass = l.polarity === 'prefer' ? 'polarity-prefer' : 'polarity-avoid';
    html += `<div class="lesson-card clickable ${polarityClass}">
      <div class="lesson-card__head">
        <div><span class="badge badge--entity">${esc(l.entity_type)}</span> <span class="lesson-card__scope">${esc(l.scope)}</span></div>
        <div class="lesson-card__stats">${polarityBadge} conf. ${(l.confidence||0).toFixed(2)} · ${l.evidence_count} confirm. · ${l.contradiction_count||0} contrad. · ${esc(l.environment)}</div>
      </div>
      <div class="lesson-card__reco">${esc(l.recommendation)}</div>
      <div>${kws}</div>
      ${sources ? `<div class="field-label">Créée / confirmée par</div><div>${sources}</div>` : ''}
    </div>`;
  });
  html += `</div>`;
  el.innerHTML = html;
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.onclick = function(e) {
      renderLessonsView(this.dataset.filter);
    };
  });
}

function goToMissionFromLessons(missionId) {
  const ep = findEpisode(missionId);
  if (!ep) { alert("Cette mission n'est plus en base (purge ou session différente)."); return; }
  selectNav('sessions');
  currentSessionId = ep.session_id;
  renderSidebar();
  openMission(missionId);
}

// =====================================================
// NAVIGATION
// =====================================================
function renderSidebar() {
  const el = document.getElementById('session-list');
  if (currentNav !== 'sessions') { el.innerHTML = ''; return; }
  let html = '';
  const sortedSessions = DATA.sessions.slice().sort((a, b) => {
    const aTime = a.turns[0] ? new Date(a.turns[0].ts).getTime() : 0;
    const bTime = b.turns[0] ? new Date(b.turns[0].ts).getTime() : 0;
    return bTime - aTime;
  });
  sortedSessions.forEach(s => {
    const active = s.session_id === currentSessionId;
    const missionCount = s.turns.filter(t => t.mode === 'mission').length;
    const directCount = s.turns.length - missionCount;
    const firstTs = s.turns[0] ? formatTimestamp(s.turns[0].ts) : '';
    html += `<div class="session-item ${active ? 'active' : ''}" onclick="selectSession('${esc(s.session_id)}')">
      <div class="session-item__id">${esc(s.session_id).slice(0, 24)}</div>
      <div class="session-item__count">${s.turns.length} tour(s) — ${missionCount} mission(s), ${directCount} direct(s)</div>
      <div class="session-item__time">${firstTs}</div>
    </div>`;
  });
  el.innerHTML = html || '<div class="empty-state">Aucune session enregistrée.</div>';
}
function selectSession(sid) {
  currentSessionId = sid;
  currentMissionId = null;
  renderSidebar();
  renderSessionThread(sid);
}
function selectNav(nav) {
  currentNav = nav;
  document.querySelectorAll('.top-nav__btn').forEach(b => b.classList.toggle('active', b.dataset.nav === nav));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + nav).classList.add('active');
  renderSidebar();
  if (nav === 'sessions' && currentSessionId) {
    if (currentMissionId) { openMission(currentMissionId); } else { renderSessionThread(currentSessionId); }
  }
}
document.querySelectorAll('.top-nav__btn').forEach(b => b.addEventListener('click', () => selectNav(b.dataset.nav)));

const genDate = DATA.generated_at ? new Date(DATA.generated_at) : null;
document.getElementById('generated-at').textContent = genDate ? 'Généré le ' + genDate.toLocaleDateString('fr-FR') + ' à ' + genDate.toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit'}) : '';
renderSidebar();
if (currentSessionId) renderSessionThread(currentSessionId);
renderLessonsView();
selectNav('sessions');
</script>
</body>
</html>
"""

# =====================================================
# MAIN
# =====================================================

def main():
    parser = argparse.ArgumentParser(description="Génère le rapport d'observabilité HTML autonome (v8.17).")
    parser.add_argument("--db", default="memory.db")
    parser.add_argument("--events", default="observability/events.jsonl")
    parser.add_argument("--out", default="observability_report.html")
    args = parser.parse_args()

    data = build_data(args.db, args.events)
    html = render_html(data)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Rapport généré : {args.out}")
    print(f"  {len(data['episodes'])} mission(s), {len(data['lessons'])} leçon(s), "
          f"{len(data['sessions'])} session(s).")

if __name__ == "__main__":
    main()
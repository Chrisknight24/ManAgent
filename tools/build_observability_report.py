#!/usr/bin/env python3
"""
build_observability_report.py (v2)
====================================
Assemble UN fichier HTML autonome (CSS + JS inline, zéro dépendance réseau,
zéro serveur) — refonte complète suite au retour utilisateur sur le v1 :

  - Thème CLAIR (le v1 était sombre, contraste jugé fatigant à la lecture).
  - Police nettement agrandie, affordance de clic explicite (les éléments
    cliquables doivent SE VOIR comme cliquables — bordure, ombre, survol —
    distincts des simples badges de statut).
  - Navigation PAR SESSION en premier niveau (pas 4 onglets plats sans lien
    entre eux) : on choisit une session, on voit le fil de conversation
    (réponses directes ET missions mêlées, comme côté frontend C++).
  - Les appels LLM ne sont plus dans un onglet séparé et déconnecté : ils
    sont rattachés à l'endroit exact où ils se sont produits dans le récit
    d'une mission (faisabilité -> Solver, plan -> Planner, convergence ->
    Executor, rapport -> Presentator), via une corrélation temporelle
    automatique entre les deux sources d'horodatage du système.
  - Les leçons affichent désormais les missions qui les ont fait naître
    (source_episodes), avec navigation directe vers la mission d'origine.

Sources de données : memory.db (episodes, lessons) + events.jsonl (Logger.event).
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


# =====================================================
# CHARGEMENT DES DONNÉES (inchangé dans le principe)
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
# CORRÉLATION TEMPORELLE (NOUVEAU)
# =====================================================
# Deux sources d'horodatage coexistent dans le système : time.time() (epoch UTC,
# utilisé par PlanAttempt/ExecutionNode) et Logger.event() (désormais UTC aussi,
# mais d'anciens fichiers events.jsonl peuvent contenir de l'heure locale naïve
# — c'est ce qui a été détecté en creusant les vraies données : 1h d'écart exact).
# Plutôt que de supposer un fuseau horaire, on le DÉTECTE par optimisation : on
# cherche le décalage qui maximise le nombre de correspondances plausibles.

PRESENTATOR_TAGS = {"generate_text", "Presentator_report", "Presentator_error"}
FEASIBILITY_TAGS = {"FeasibilityDecision"}
PLANNING_TAGS = {"Plan", "RerankedLessons"}
CONVERGENCE_TAGS = {"ConvergenceDecision"}


def _parse_ts_raw(ts) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


def _count_matches(episodes: List[Dict], llm_calls: List[Dict], offset: float) -> int:
    count = 0

    def walk(tree):
        nonlocal count
        t_start = tree.get("started_at")
        attempts = tree.get("attempts", [])
        first_attempt_start = attempts[0]["started_at"] if attempts else None
        for call in llm_calls:
            ts = _parse_ts_raw(call.get("ts"))
            if ts is None or t_start is None:
                continue
            ts -= offset
            upper = first_attempt_start if first_attempt_start is not None else t_start + 3600
            if (t_start - 3) <= ts < (upper + 1) and call.get("tag") in FEASIBILITY_TAGS:
                count += 1
        for attempt in attempts:
            a_start = attempt.get("started_at")
            nodes = attempt.get("nodes", [])
            first_node_start = nodes[0]["started_at"] if nodes else None
            a_end = attempt.get("ended_at") or a_start
            for call in llm_calls:
                ts = _parse_ts_raw(call.get("ts"))
                if ts is None or a_start is None:
                    continue
                ts -= offset
                upper = first_node_start if first_node_start is not None else a_end
                if (a_start - 3) <= ts < (upper + 1) and call.get("tag") in PLANNING_TAGS:
                    count += 1
            for node in nodes:
                if node.get("child_execution_tree"):
                    walk(node["child_execution_tree"])

    for ep in episodes:
        walk(ep.get("execution_tree") or {})
    return count


def detect_clock_offset(episodes: List[Dict], llm_calls: List[Dict]) -> float:
    """Recherche par pas de 15 min sur +/- 14h le décalage qui maximise les correspondances."""
    if not episodes or not llm_calls:
        return 0.0
    best_offset, best_score = 0.0, -1
    for step in range(-56, 57):
        offset = step * 15 * 60
        score = _count_matches(episodes, llm_calls, offset)
        if score > best_score:
            best_score, best_offset = score, offset
    return best_offset


def correlate_tree(tree: Dict, all_calls: List[Dict], consumed: set, offset: float):
    """Rattache récursivement à l'arbre les appels LLM qui lui appartiennent (mutation en place)."""
    def ts_of(call):
        raw = _parse_ts_raw(call.get("ts"))
        return None if raw is None else raw - offset

    t_start = tree.get("started_at")
    attempts = tree.get("attempts", [])
    first_attempt_start = attempts[0]["started_at"] if attempts else None

    feasibility_calls = []
    for i, call in enumerate(all_calls):
        if i in consumed:
            continue
        ts = ts_of(call)
        if ts is None or t_start is None:
            continue
        upper = first_attempt_start if first_attempt_start is not None else t_start + 3600
        if (t_start - 3) <= ts < (upper + 1) and call.get("tag") in FEASIBILITY_TAGS:
            feasibility_calls.append(call)
            consumed.add(i)
    tree["_feasibility_calls"] = feasibility_calls

    for attempt in attempts:
        a_start = attempt.get("started_at")
        a_end = attempt.get("ended_at") or a_start
        nodes = attempt.get("nodes", [])
        first_node_start = nodes[0]["started_at"] if nodes else None

        planning_calls = []
        for i, call in enumerate(all_calls):
            if i in consumed:
                continue
            ts = ts_of(call)
            if ts is None or a_start is None:
                continue
            upper = first_node_start if first_node_start is not None else a_end
            if (a_start - 3) <= ts < (upper + 1) and call.get("tag") in PLANNING_TAGS:
                planning_calls.append(call)
                consumed.add(i)
        attempt["_planning_calls"] = planning_calls

        for node in nodes:
            n_start = node.get("started_at")
            n_end = node.get("ended_at") or n_start
            child = node.get("child_execution_tree")
            if child:
                correlate_tree(child, all_calls, consumed, offset)
            node_calls = []
            for i, call in enumerate(all_calls):
                if i in consumed:
                    continue
                ts = ts_of(call)
                if ts is None or n_start is None:
                    continue
                if (n_start - 3) <= ts <= (n_end + 3) and call.get("tag") in CONVERGENCE_TAGS:
                    node_calls.append(call)
                    consumed.add(i)
            node["_node_calls"] = node_calls


def attach_presentator_and_routing(episodes, sessions_turns, all_calls, consumed, offset):
    def ts_of(call):
        raw = _parse_ts_raw(call.get("ts"))
        return None if raw is None else raw - offset

    for ep in episodes:
        tree = ep.get("execution_tree") or {}
        t_end = tree.get("ended_at")
        pres_calls = []
        for i, call in enumerate(all_calls):
            if i in consumed:
                continue
            ts = ts_of(call)
            if ts is None or t_end is None:
                continue
            if t_end <= ts <= t_end + 120 and call.get("tag") in PRESENTATOR_TAGS:
                pres_calls.append(call)
                consumed.add(i)
        ep["_presentator_calls"] = pres_calls

    # OrchestratorDecision (routage) : rattaché au tour de session le plus proche en temps
    for turn in sessions_turns:
        turn_ts_raw = _parse_ts_raw(turn.get("ts"))
        turn_ts = None if turn_ts_raw is None else turn_ts_raw - offset
        best_i, best_dist = None, None
        for i, call in enumerate(all_calls):
            if i in consumed or call.get("tag") != "OrchestratorDecision":
                continue
            ts = ts_of(call)
            if ts is None or turn_ts is None:
                continue
            dist = abs(ts - turn_ts)
            if dist < 30 and (best_dist is None or dist < best_dist):
                best_i, best_dist = i, dist
        if best_i is not None:
            turn["_routing_call"] = all_calls[best_i]
            consumed.add(best_i)


def build_data(db_path: str, events_path: str) -> Dict[str, Any]:
    episodes = load_episodes(db_path)
    lessons = load_lessons(db_path)
    events = load_events(events_path)

    session_turns = [e for e in events if e.get("event") == "session_turn"]
    llm_calls = [e for e in events if e.get("event") == "llm_call"]

    offset = detect_clock_offset(episodes, llm_calls)
    consumed: set = set()
    for ep in episodes:
        correlate_tree(ep["execution_tree"], llm_calls, consumed, offset)
    attach_presentator_and_routing(episodes, session_turns, llm_calls, consumed, offset)

    # Ce qui reste (essentiellement les analyses du Learner, ExtractedLesson) : pas perdu,
    # juste rangé à part — ces appels ne concernent aucune mission précise.
    unattached_calls = [c for i, c in enumerate(llm_calls) if i not in consumed]

    sessions: Dict[str, List[Dict[str, Any]]] = {}
    for turn in session_turns:
        sessions.setdefault(turn.get("session_id", "?"), []).append(turn)
    for turns in sessions.values():
        turns.sort(key=lambda t: t.get("ts") or "")

    return {
        "episodes": episodes,
        "lessons": lessons,
        "sessions": [{"session_id": sid, "turns": turns} for sid, turns in sessions.items()],
        "unattached_calls": unattached_calls,
        "clock_offset_detected": offset,
    }

# =====================================================
# GABARIT HTML — thème clair, grande police, affordance de clic explicite
# =====================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Observabilité — ManAgent</title>
<style>
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

/* --- SIDEBAR : sessions en premier niveau --- */
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
.session-item:hover { border-color: var(--accent); box-shadow: var(--shadow-md); transform: translateY(-1px); }
.session-item.active { border-color: var(--accent); background: var(--accent-bg); box-shadow: var(--shadow-md); }
.session-item__id { font-family: var(--mono); font-size: 12.5px; color: var(--text-muted); }
.session-item__count { font-size: 14.5px; font-weight: 700; margin-top: 3px; }
.session-item__time { font-size: 11.5px; color: var(--text-faint); margin-top: 3px; font-family: var(--mono); }

/* --- MAIN --- */
.main { flex: 1; overflow-y: auto; padding: 32px 40px 80px; }
.view { display: none; }
.view.active { display: block; }
.empty-state {
  color: var(--text-faint); font-size: 16px; padding: 60px 0; text-align: center;
  border: 2px dashed var(--border); border-radius: 14px; background: var(--surface);
}

h2.section-title { font-size: 26px; margin: 0 0 22px; font-weight: 800; }
h3.sub-title { font-size: 15px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin: 22px 0 10px; }

/* --- Badges (statut = FLAT, jamais cliquables, pas d'ombre) --- */
.badge {
  display: inline-block; padding: 4px 11px; border-radius: 999px; font-size: 12.5px;
  font-weight: 800; font-family: var(--mono); letter-spacing: 0.01em; white-space: nowrap;
}
.badge--success { background: var(--success-bg); color: var(--success); }
.badge--failed { background: var(--failure-bg); color: var(--failure); }
.badge--skipped { background: var(--skipped-bg); color: var(--skipped); }
.badge--pending { background: var(--pending-bg); color: var(--pending); }
.badge--entity { background: var(--accent-bg); color: var(--accent); }
.badge--env-real { background: var(--failure-bg); color: var(--failure); }
.badge--env-simulated { background: var(--pending-bg); color: var(--pending); }

.dot { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 9px; flex-shrink: 0; }
.dot--success { background: var(--success); }
.dot--failed { background: var(--failure); }
.dot--skipped { background: var(--skipped); }
.dot--pending { background: var(--pending); }

/* --- Éléments CLIQUABLES : affordance forte et cohérente partout --- */
.clickable {
  cursor: pointer; background: var(--surface); border: 1.5px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow-sm); transition: all .12s ease;
}
.clickable:hover { border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }
.clickable:active { transform: translateY(0); box-shadow: var(--shadow-sm); }

/* --- Fil de session (style chat) --- */
.thread-turn { margin-bottom: 22px; }
.thread-turn__user {
  background: var(--surface-alt); border-radius: 14px 14px 4px 14px; padding: 13px 18px;
  max-width: 75%; margin-left: auto; font-size: 16px; font-weight: 600; box-shadow: var(--shadow-sm);
}
.thread-turn__meta { font-size: 12px; color: var(--text-faint); font-family: var(--mono); margin: 6px 4px; text-align: right; }
.thread-turn__response {
  background: var(--surface); border-radius: 14px 14px 14px 4px; padding: 16px 20px;
  max-width: 85%; box-shadow: var(--shadow-sm); border: 1.5px solid var(--border); margin-top: 8px;
}
.thread-turn__badge-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.responder-tag { font-family: var(--mono); font-size: 12px; font-weight: 800; color: var(--accent); }

/* --- Carte de mission (cliquable, dans le fil) --- */
.mission-card {
  padding: 16px 20px; margin-top: 8px; max-width: 85%;
}
.mission-card__title { font-size: 17px; font-weight: 700; margin-bottom: 8px; }
.mission-card__row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mission-card__hint { margin-top: 10px; font-size: 13.5px; color: var(--accent); font-weight: 700; }
.mission-card__hint::after { content: " →"; }

/* --- Vue mission détaillée : narration par entité --- */
.mission-detail { background: var(--surface); border: 1.5px solid var(--border); border-radius: 14px; padding: 24px 28px; box-shadow: var(--shadow-sm); }
.back-link { display: inline-block; margin-bottom: 16px; font-weight: 700; cursor: pointer; color: var(--accent); }
.back-link:hover { text-decoration: underline; }

.mission-header__goal { font-size: 22px; font-weight: 800; margin-bottom: 8px; }
.mission-header__meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; color: var(--text-faint); font-family: var(--mono); margin-bottom: 18px; padding-bottom: 18px; border-bottom: 1.5px solid var(--border); }

.entity-block {
  border-left: 4px solid var(--border-strong); padding: 4px 0 4px 18px; margin: 14px 0 14px 6px;
}
.entity-block--solver { border-left-color: #4d5770; }
.entity-block--planner { border-left-color: var(--accent); }
.entity-block--executor { border-left-color: #0f7a3d; }
.entity-block--presentator { border-left-color: var(--accent-2); }
.entity-block__label {
  font-family: var(--mono); font-size: 12.5px; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 6px;
}

details.attempt, details.step, details.llm-call {
  border: 1.5px solid var(--border); border-radius: 10px; background: var(--surface);
  margin: 8px 0; box-shadow: var(--shadow-sm); transition: box-shadow .12s ease;
}
details.attempt:hover, details.step:hover, details.llm-call:hover { box-shadow: var(--shadow-md); }
details.attempt > summary, details.step > summary, details.llm-call > summary {
  padding: 12px 16px; cursor: pointer; list-style: none; display: flex; align-items: center;
  gap: 10px; font-size: 15px; font-weight: 600;
}
details > summary::-webkit-details-marker { display: none; }
.chevron {
  display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px;
  border-radius: 6px; background: var(--accent-bg); color: var(--accent); font-size: 12px;
  font-weight: 900; flex-shrink: 0; transition: transform .12s ease;
}
details[open] > summary .chevron { transform: rotate(90deg); }
.attempt-body, .step-body, .llm-call-body { padding: 6px 18px 16px 46px; font-size: 15px; color: var(--text-muted); border-top: 1.5px solid var(--border); margin-top: 2px; padding-top: 14px; }
.step-title { font-weight: 700; color: var(--text); }
.step-tool { font-family: var(--mono); font-size: 12.5px; color: var(--accent); background: var(--accent-bg); padding: 2px 8px; border-radius: 6px; }

.raw-tolerated {
  display: inline-block; margin-top: 8px; padding: 6px 11px; border-radius: 8px;
  background: var(--skipped-bg); color: var(--skipped); font-size: 12.5px; font-family: var(--mono); font-weight: 700;
}
.advice-box {
  margin-top: 10px; padding: 12px 16px; border-radius: 10px; background: var(--accent-2-bg);
  border: 1.5px solid #dcc2f2; font-size: 14px; white-space: pre-wrap; color: #5a1f8a;
}
.advice-box__label { font-family: var(--mono); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: #5a1f8a; margin-bottom: 5px; font-weight: 800; }
.child-tree-wrap { margin-top: 10px; padding-left: 16px; border-left: 3px dashed var(--border-strong); }

.prompt-block {
  background: var(--surface-alt); border: 1.5px solid var(--border); border-radius: 8px; padding: 12px 14px;
  font-family: var(--mono); font-size: 13.5px; white-space: pre-wrap; max-height: 360px; overflow-y: auto;
  margin-top: 8px; color: var(--text);
}
.field-label { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); margin-top: 14px; font-weight: 800; }
.llm-call__tag { font-weight: 800; font-family: var(--mono); }
.llm-call__duration { font-family: var(--mono); font-size: 13px; color: var(--text-faint); margin-left: auto; }

/* --- Leçons --- */
.lesson-card { padding: 16px 20px; margin-bottom: 12px; }
.lesson-card__head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.lesson-card__scope { font-family: var(--mono); font-weight: 800; font-size: 15.5px; }
.lesson-card__stats { font-family: var(--mono); font-size: 12.5px; color: var(--text-faint); white-space: nowrap; }
.lesson-card__reco { margin-top: 8px; font-size: 16px; }
.kw-tag {
  display: inline-block; background: var(--surface-alt); color: var(--text-muted); font-size: 12px;
  padding: 3px 9px; border-radius: 6px; margin: 8px 6px 0 0; font-family: var(--mono);
}
.source-ep-link {
  display: inline-block; margin: 8px 6px 0 0; padding: 4px 10px; border-radius: 7px;
  background: var(--accent-bg); color: var(--accent); font-size: 12.5px; font-weight: 700;
  cursor: pointer; border: 1.5px solid transparent;
}
.source-ep-link:hover { border-color: var(--accent); box-shadow: var(--shadow-sm); }
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="sidebar__header">
      <div class="sidebar__title">Observabilité</div>
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
let currentNav = 'sessions';
let currentSessionId = DATA.sessions[0] ? DATA.sessions[0].session_id : null;
let currentMissionId = null;

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
  const cls = {success: 'success', failed: 'failed', skipped: 'skipped', pending: 'pending'}[status] || 'pending';
  return `<span class="badge badge--${cls}">${esc(status || '?')}</span>`;
}
function envBadge(env) {
  const cls = env === 'real' ? 'env-real' : 'env-simulated';
  return `<span class="badge badge--${cls}">${esc(env || 'simulated')}</span>`;
}
function findEpisode(missionId) { return DATA.episodes.find(e => e.mission_id === missionId); }

// =====================================================
// APPELS LLM (rendu générique, réutilisé partout où on en affiche)
// =====================================================
function renderLlmCall(c) {
  const ok = c.success !== false;
  return `<details class="llm-call">
    <summary>
      <span class="chevron">▸</span>
      <span class="llm-call__tag" style="color:${ok ? 'var(--accent)' : 'var(--failure)'}">${esc(c.tag || c.schema || '?')}</span>
      ${statusBadge(ok ? 'success' : 'failed')}
      <span class="llm-call__duration">${c.duration_ms !== undefined ? c.duration_ms + ' ms' : ''}</span>
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
// ARBRE D'EXÉCUTION — narration par entité
// =====================================================
function renderNode(node) {
  const statusCls = node.status || 'pending';
  let toleratedBadge = '';
  if (node.raw_tool_success === false && node.status === 'success') {
    toleratedBadge = `<div class="raw-tolerated">⚠ outil brut = false, toléré (expected_result="any")</div>`;
  } else if (node.raw_tool_success === true && node.status === 'failed') {
    toleratedBadge = `<div class="raw-tolerated">ℹ outil brut = true, mais convergence jugée non satisfaite</div>`;
  }
  let childHtml = '';
  if (node.child_execution_tree) {
    childHtml = `<div class="child-tree-wrap">${renderSolverBlock(node.child_execution_tree)}</div>`;
  }
  const nodeCalls = node._node_calls || [];
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
      ${childHtml}
    </div>
  </details>`;
}

function renderAttempt(attempt, idx) {
  let adviceHtml = '';
  if (attempt.advice_injected) {
    adviceHtml = `<div class="advice-box"><div class="advice-box__label">Conseil injecté dans ce plan</div>${esc(attempt.advice_injected)}</div>`;
  }
  const entityBadge = attempt.target_entity ? `<span class="badge badge--entity">${esc(attempt.target_entity)}</span>` : '';
  const fcBadge = (attempt.failure_class && attempt.failure_class !== 'none') ? `<span class="badge badge--failed">${esc(attempt.failure_class)}</span>` : '';
  const nodesHtml = (attempt.nodes || []).map(n => renderNode(n)).join('');
  const planningCalls = attempt._planning_calls || [];
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

function renderSolverBlock(tree) {
  const feasCalls = tree._feasibility_calls || [];
  const attempts = (tree.attempts || []).map((a, i) => renderAttempt(a, i)).join('');
  return `<div>
    <div style="font-size:13px;color:var(--text-faint);font-family:var(--mono);margin-bottom:8px">
      Solver [${esc(tree.solver_id)}] — statut final : ${esc(tree.status)}
    </div>
    <div class="entity-block entity-block--solver">
      <div class="entity-block__label">🧭 Solver — évaluation de faisabilité</div>
      ${feasCalls.length ? renderLlmCalls(feasCalls) : '<div style="color:var(--text-faint);font-size:13.5px">Aucun appel capturé.</div>'}
    </div>
    ${attempts}
  </div>`;
}

function renderMissionDetail(missionId) {
  const ep = findEpisode(missionId);
  if (!ep) return '<div class="empty-state">Mission introuvable.</div>';
  let html = `<div class="back-link" onclick="backToSession()">← Retour au fil de la session</div>`;
  html += `<div class="mission-detail">
    <div class="mission-header__goal">${esc(ep.goal)}</div>
    <div class="mission-header__meta">
      ${statusBadge(ep.status)} ${envBadge(ep.environment)}
      <span>créée : ${esc(ep.created_at)}</span>
      <span>finie : ${esc(ep.finished_at || '—')}</span>
      <span>${ep.analyzed_at ? 'analysée le ' + esc(ep.analyzed_at) : 'pas encore analysée'}</span>
    </div>`;
  html += renderSolverBlock(ep.execution_tree);

  const presCalls = ep._presentator_calls || [];
  html += `<div class="entity-block entity-block--presentator">
    <div class="entity-block__label">🗣️ Presentator — rédaction du rapport final</div>
    ${ep.presentator_result ? `<div style="margin-bottom:8px">${statusBadge(ep.presentator_result.status)} ${ep.presentator_result.status === 'failed' ? esc(ep.presentator_result.error_reason) : ''}</div>` : ''}
    ${presCalls.length ? renderLlmCalls(presCalls) : '<div style="color:var(--text-faint);font-size:13.5px">Aucun appel capturé.</div>'}
  </div>`;

  if (ep.resolved_data && Object.keys(ep.resolved_data).length > 0) {
    html += `<div class="field-label">Registre de variables résolu</div><div class="prompt-block">${esc(fmtJson(ep.resolved_data))}</div>`;
  }
  html += `</div>`;
  return html;
}

// =====================================================
// FIL DE SESSION (style chat)
// =====================================================
function renderSessionThread(sessionId) {
  const session = DATA.sessions.find(s => s.session_id === sessionId);
  if (!session) return '<div class="empty-state">Aucune session sélectionnée.</div>';

  let html = `<h2 class="section-title">Session <span style="font-family:var(--mono);font-size:18px;color:var(--text-faint)">${esc(sessionId)}</span></h2>`;
  session.turns.forEach(t => {
    html += `<div class="thread-turn">
      <div class="thread-turn__user">${esc(t.user_message || '')}</div>
      <div class="thread-turn__meta">${esc(t.ts || '')}</div>`;
    if (t._routing_call) {
      html += `<div style="max-width:85%;margin-top:6px">
        <div class="field-label" style="margin-top:0">🧭 Orchestrator — décision de routage (direct ou mission ?)</div>
        ${renderLlmCall(t._routing_call)}
      </div>`;
    }
    if (t.mode === 'direct') {
      html += `<div class="thread-turn__response">
        <div class="thread-turn__badge-row"><span class="responder-tag">ORCHESTRATOR · réponse directe</span></div>
        ${esc(t.response || '')}
      </div>`;
    } else {
      const ep = findEpisode(t.mission_id);
      html += `<div class="thread-turn__response mission-card clickable" onclick="openMission('${esc(t.mission_id)}')">
        <div class="thread-turn__badge-row"><span class="responder-tag">MISSION</span> ${ep ? statusBadge(ep.status) : statusBadge('pending')}</div>
        <div class="mission-card__title">${esc(t.refined_goal || ep && ep.goal || '')}</div>
        <div class="mission-card__hint">Voir le déroulé complet (Planner, Executor, Presentator)</div>
      </div>`;
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
function renderLessonsView() {
  const el = document.getElementById('view-lessons');
  if (DATA.lessons.length === 0) {
    el.innerHTML = '<h2 class="section-title">Leçons</h2><div class="empty-state">Aucune leçon en base.</div>';
    return;
  }
  let html = `<h2 class="section-title">Base de leçons (${DATA.lessons.length})</h2>`;
  DATA.lessons.forEach(l => {
    const kws = (l.keywords || []).map(k => `<span class="kw-tag">${esc(k)}</span>`).join('');
    const sources = (l.source_episodes || []).map(mid => {
      const ep = findEpisode(mid);
      const label = ep ? ep.goal : mid;
      return `<span class="source-ep-link" onclick="goToMissionFromLessons('${esc(mid)}')">↳ ${esc((label||'').slice(0,40))}</span>`;
    }).join('');
    html += `<div class="lesson-card clickable">
      <div class="lesson-card__head">
        <div><span class="badge badge--entity">${esc(l.entity_type)}</span> <span class="lesson-card__scope">${esc(l.scope)}</span></div>
        <div class="lesson-card__stats">conf. ${(l.confidence||0).toFixed(2)} · ${l.evidence_count} confirm. · ${l.contradiction_count||0} contrad. · ${esc(l.environment)}</div>
      </div>
      <div class="lesson-card__reco">${esc(l.recommendation)}</div>
      <div>${kws}</div>
      ${sources ? `<div class="field-label">Créée / confirmée par</div><div>${sources}</div>` : ''}
    </div>`;
  });
  el.innerHTML = html;
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
  DATA.sessions.forEach(s => {
    const active = s.session_id === currentSessionId;
    const missionCount = s.turns.filter(t => t.mode === 'mission').length;
    const directCount = s.turns.length - missionCount;
    html += `<div class="session-item ${active ? 'active' : ''}" onclick="selectSession('${esc(s.session_id)}')">
      <div class="session-item__id">${esc(s.session_id).slice(0, 24)}</div>
      <div class="session-item__count">${s.turns.length} tour(s) — ${missionCount} mission(s), ${directCount} direct(s)</div>
      <div class="session-item__time">${esc((s.turns[0]||{}).ts || '')}</div>
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

document.getElementById('generated-at').textContent = DATA.generated_at || '';
renderSidebar();
if (currentSessionId) renderSessionThread(currentSessionId);
renderLessonsView();
selectNav('sessions');
</script>
</body>
</html>
"""


def render_html(data: Dict[str, Any]) -> str:
    data = {**data, "generated_at": datetime.now().isoformat(timespec="seconds")}
    json_blob = json.dumps(data, ensure_ascii=False, default=str)
    json_blob = json_blob.replace("</script", "<\\/script")
    return HTML_TEMPLATE.replace("__DATA_JSON__", json_blob)


def main():
    parser = argparse.ArgumentParser(description="Génère le rapport d'observabilité HTML autonome (v2).")
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
    print(f"  Décalage d'horloge détecté et compensé : {data['clock_offset_detected']/3600:.2f}h")
    print(f"  Appels LLM non rattachés à une mission précise (analyse Learner, etc.) : {len(data['unattached_calls'])}")


if __name__ == "__main__":
    main()
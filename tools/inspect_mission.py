#!/usr/bin/env python3
"""
inspect_mission.py
===================
Outil de diagnostic en ligne de commande : lit memory.db (SQLite) et affiche
un rapport lisible, sans avoir à reconstruire le fil d'une mission à la main
depuis la console. Ne dépend que de la stdlib (pas de pydantic requis) :
il lit directement le JSON stocké, il n'a pas besoin des modèles du moteur.

Usage :
    python inspect_mission.py                       # dernières 5 missions
    python inspect_mission.py --limit 10             # dernières 10 missions
    python inspect_mission.py --mission-id XXXX      # une mission précise
    python inspect_mission.py --lessons              # uniquement la table lessons
    python inspect_mission.py --db chemin/memory.db  # si pas dans le dossier courant
"""
import argparse
import json
import sqlite3


def format_episode_header(ep: dict) -> str:
    return (
        f"\n{'=' * 78}\n"
        f"MISSION {ep['mission_id']}  (session={ep.get('session_id')})\n"
        f"  goal        : {ep.get('goal')}\n"
        f"  status      : {ep.get('status')}   environment={ep.get('environment')}\n"
        f"  créée       : {ep.get('created_at')}   finie : {ep.get('finished_at')}\n"
        f"  analysée le : {ep.get('analyzed_at') or '— pas encore analysée —'}\n"
        f"{'=' * 78}"
    )


def print_node(node: dict, indent: int = 0):
    pad = "  " * indent
    status = node.get("status")
    marker = {"success": "[OK]", "failed": "[XX]", "skipped": "[--]", "pending": "[..]"}.get(status, "[??]")
    print(f"{pad}{marker} [{node.get('step_id')}] ({node.get('step_type')}) {node.get('description')}")
    if node.get("tool_name"):
        print(f"{pad}      outil: {node['tool_name']}")
    if status == "failed" and node.get("error_reason"):
        print(f"{pad}      raison: {node.get('error_reason')}")
    child_tree = node.get("child_execution_tree")
    if child_tree:
        print(f"{pad}      +-- sous-solver [{child_tree.get('solver_id')}] :")
        print_tree(child_tree, indent + 3)


def print_attempt(attempt: dict, indent: int = 1):
    pad = "  " * indent
    outcome = attempt.get("outcome")
    marker = "[OK]" if outcome == "success" else "[XX]"
    fc = attempt.get("failure_class")
    te = attempt.get("target_entity")
    line = f"{pad}{marker} Tentative #{attempt.get('attempt_number')} - outcome={outcome}"
    if outcome != "success":
        entity_flag = te if te else "*** NON DEFINI ***"
        line += f"  failure_class={fc}  target_entity={entity_flag}"
    print(line)
    if attempt.get("failure_reason"):
        print(f"{pad}    raison: {attempt['failure_reason']}")
    for node in attempt.get("nodes", []):
        print_node(node, indent + 1)


def print_tree(tree: dict, indent: int = 0):
    pad = "  " * indent
    print(f"{pad}Solver [{tree.get('solver_id')}] - but: {tree.get('goal')} - statut final: {tree.get('status')}")
    for attempt in tree.get("attempts", []):
        print_attempt(attempt, indent + 1)


def inspect_episode(ep: dict):
    print(format_episode_header(ep))
    tree_json = ep.get("execution_tree_json")
    if tree_json and tree_json != "{}":
        try:
            tree = json.loads(tree_json)
            print_tree(tree)
        except Exception as e:
            print(f"  [ERREUR parsing execution_tree_json] {e}")
    else:
        print("  (arbre d'exécution vide)")

    pres_json = ep.get("presentator_result_json")
    if pres_json and pres_json not in ("{}", "null", ""):
        try:
            pres = json.loads(pres_json)
            if pres:
                status = pres.get("status")
                marker = "[OK]" if status == "success" else "[XX]"
                extra = f" - {pres.get('error_reason')}" if status == "failed" else ""
                print(f"\n  {marker} Presentator: {status}{extra}")
        except Exception:
            pass


def dump_lessons(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT entity_type, scope, recommendation, confidence, evidence_count,
                   contradiction_count, environment, is_active
            FROM lessons ORDER BY entity_type, confidence DESC
        """)
    except sqlite3.OperationalError as e:
        print(f"\n(table 'lessons' introuvable ou vide : {e})")
        return
    rows = cur.fetchall()
    print(f"\n{'=' * 78}\nTABLE LESSONS ({len(rows)} ligne(s))\n{'=' * 78}")
    if not rows:
        print("Aucune leçon en base pour l'instant.")
        return
    for r in rows:
        entity_type, scope, reco, conf, ev, contra, env, active = r
        flag = "(actif) " if active else "(inactif)"
        print(f"{flag} [{entity_type}] {scope}  (env={env}, conf={conf:.2f}, evidence={ev}, contradiction={contra})")
        print(f"          -> {reco}")


def main():
    parser = argparse.ArgumentParser(description="Diagnostic memory.db (episodes + lessons)")
    parser.add_argument("--db", default="memory.db")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--mission-id", default=None)
    parser.add_argument("--lessons", action="store_true", help="Afficher uniquement la table lessons")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.lessons:
        dump_lessons(conn)
        return

    cur = conn.cursor()
    if args.mission_id:
        cur.execute("SELECT * FROM episodes WHERE mission_id = ?", (args.mission_id,))
    else:
        cur.execute("SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", (args.limit,))
    rows = cur.fetchall()
    if not rows:
        print("Aucun épisode trouvé dans 'episodes'.")
    for row in rows:
        inspect_episode(dict(row))

    dump_lessons(conn)


if __name__ == "__main__":
    main()
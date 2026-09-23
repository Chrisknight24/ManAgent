"""
minimal_host.py — Le plus petit hôte possible pour ManAgent (exemple qui tourne).

But : montrer en 60 lignes comment parler à ManAgent, sans clé API.
On lance le runtime, on attend "je suis prêt", on se présente,
on lui demande son catalogue de modèles. C'est tout.

Lancer depuis la racine du repo :
    .venv\\Scripts\\python.exe examples\\minimal_host.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def send(proc, action, payload=None, _counter=[0]):
    """Envoie une demande (une ligne JSON) et rend son numéro."""
    _counter[0] += 1
    packet = {"id": f"req_{_counter[0]:03d}", "type": "request",
              "action": action, "payload": payload or {}}
    proc.stdin.write(json.dumps(packet) + "\n")
    proc.stdin.flush()
    return packet["id"]


def read_json_lines(proc, want_ids, timeout_s=60):
    """Lit les réponses jusqu'à avoir tous les numéros voulus."""
    import time
    got = {}
    end = time.time() + timeout_s
    while set(want_ids) - set(got) and time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line.startswith("{"):
            continue  # les logs ne nous intéressent pas
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "event" and obj.get("event") == "runtime.ready":
            print("ManAgent : je suis prêt.")
        if obj.get("id") in want_ids:
            got[obj["id"]] = obj
    return got


def main():
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(ROOT), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )
    try:
        # 1. Se présenter : qui on est + nos 2 faux outils.
        manifest = {
            "host_name": "mini-host-exemple",
            "host_version": "0.1.0",
            "capabilities": ["demo"],
            "tools": [
                {"name": "dire_bonjour",
                 "description": "Répond bonjour à quelqu'un.",
                 "parameters": {"type": "object",
                               "properties": {"prenom": {"type": "string"}},
                               "required": ["prenom"]}},
                {"name": "additionner",
                 "description": "Additionne deux nombres.",
                 "parameters": {"type": "object",
                               "properties": {"a": {"type": "number"},
                                              "b": {"type": "number"}},
                               "required": ["a", "b"]}},
            ],
            "environment": {"os": "demo"},
        }
        # 2. Configurer (sans clé API : pas de mission IA dans cette démo).
        ids = [send(proc, "runtime.configure",
                    {"system_prompt": "Tu es un assistant de démo.",
                     "language": "fr", "protocol_version": "1",
                     "host_manifest": manifest,
                     "embeddings": {"mode": "lite"}})]
        # 3. Demander le catalogue des modèles + les stats mémoire.
        ids += [send(proc, "embeddings.catalog", {}),
                send(proc, "data.stats", {})]
        answers = read_json_lines(proc, ids)
        for rid in ids:
            ans = answers.get(rid, {})
            print(f"→ {rid} : {ans.get('status', 'PAS DE RÉPONSE')}")
        models = answers.get(ids[1], {}).get("payload", {}).get("models", [])
        print(f"Modèles connus : {[m['id'] for m in models]}")
        print("Démo finie. Prochaine étape : ajouter une clé API et envoyer chat.send.")
    finally:
        # Fermer l'entrée = le runtime s'arrête tout seul (pas besoin de le tuer).
        try:
            proc.stdin.close()
            proc.wait(timeout=15)
        except Exception:
            pass


if __name__ == "__main__":
    main()

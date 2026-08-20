"""
logger.py
==========

Système de logs centralisé du runtime.

IMPORTANT :
-------------
Dans notre architecture :

stdout :
    réservé EXCLUSIVEMENT
    aux packets JSON du protocole.

stderr :
    réservé aux logs/debug.

Pourquoi ?
-----------
Parce que sinon :
les logs casseraient le protocole JSON.

Exemple dangereux :
-------------------
print("debug")

Cela polluerait stdout
et le client Qt recevrait :
    - du JSON
    - ET du texte parasite

=> parsing cassé.

Donc :
    stdout = communication officielle
    stderr = logs/debug
"""

import sys
import os
import json
import threading
from datetime import datetime, timezone

# =========================================================
# RUNTIME STATE REFERENCE
# =========================================================

_runtime_state_ref = None

# NOTE (correctif observabilité) : il existait ici une fonction
# `_get_current_mission_id()` qui lisait `runtime_state.current_mission_id`,
# un attribut global JAMAIS remis à None après la fin d'une mission. Résultat :
# une fois qu'une mission avait tourné, TOUT événement émis ensuite (y compris
# pour des tours directs sans rapport) héritait silencieusement de l'ancien
# mission_id. Elle est supprimée : `mission_id` est désormais injecté
# exclusivement via `execution_context` ci-dessous (bloc "injection du
# contexte d'exécution"), qui est correctement scopé et se nettoie tout seul
# à la sortie de chaque `with execution_context.scope(...)`. Un événement émis
# hors de tout scope portant mission_id aura donc, à juste titre, mission_id
# absent plutôt qu'une valeur périmée.

# =========================================================
# LOGGER CLASS
# =========================================================

class Logger:
    _json_sink_path: str = None
    _lock = threading.Lock()

    @staticmethod
    def set_runtime_state(runtime_state):
        """
        Enregistre une référence au RuntimeState pour que Logger.event()
        puisse injecter automatiquement current_mission_id.
        À appeler une fois au démarrage du runtime.
        """
        global _runtime_state_ref
        _runtime_state_ref = runtime_state

    @staticmethod
    def configure_json_sink(path: str):
        """
        Active l'écriture d'événements structurés (JSONL) vers `path`.
        """
        Logger._json_sink_path = path
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        except Exception as e:
            Logger._log("WARNING", f"[Logger] Impossible de préparer le sink JSON ({path}) : {e}")

    @staticmethod
    def _log(level: str, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        final_message = f"[{timestamp}] [{level}] {message}"
        print(final_message, file=sys.stderr, flush=True)

    @staticmethod
    def info(message: str):
        Logger._log("INFO", message)

    @staticmethod
    def warning(message: str):
        Logger._log("WARNING", message)

    @staticmethod
    def error(message: str):
        Logger._log("ERROR", message)

    @staticmethod
    def debug(message: str):
        Logger._log("DEBUG", message)

    @staticmethod
    def event(event_type: str, **fields):
        # OBSERVABILITY : injection du contexte d'exécution.
        # mission_id, turn_id, solver_id, attempt_number, step_id,
        # discovery_run_id, entity_id, entity_name, entity_role, span_id,
        # parent_span_id... tout ce qui a été posé par un `execution_context
        # .scope(...)` ambiant est injecté ici automatiquement, SAUF si
        # l'appelant a déjà fourni explicitement la même clé (auquel cas la
        # valeur explicite gagne toujours).
        if _runtime_state_ref:
            ctx_dict = _runtime_state_ref.execution_context.to_dict()
            for key, value in ctx_dict.items():
                if key not in fields:  # ne pas écraser si déjà fourni explicitement
                    fields[key] = value

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
        }
        record.update(fields)

        # Résumé texte (pour stderr)
        try:
            summary = json.dumps(fields, ensure_ascii=False, default=str)
        except Exception:
            summary = str(fields)
        if len(summary) > 300:
            summary = summary[:300] + "…"
        Logger._log("EVENT", f"{event_type} :: {summary}")

        # Écriture dans le sink JSON
        if Logger._json_sink_path:
            try:
                line = json.dumps(record, ensure_ascii=False, default=str)
                with Logger._lock:
                    with open(Logger._json_sink_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            except Exception as e:
                Logger._log("WARNING", f"[Logger] Échec d'écriture du sink JSON (non bloquant) : {e}")
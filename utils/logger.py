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


# =========================================================
# IMPORTS
# =========================================================

import sys
import os
import json
import threading


# datetime :
# -----------
# Sert à générer timestamps/date/heure.
#
from datetime import datetime, timezone, timezone


# =========================================================
# LOGGER CLASS
# =========================================================

class Logger:
    """
    Logger centralisé du runtime.

    Pourquoi centraliser les logs ?
    --------------------------------
    Parce que plus tard :
        - observability
        - traces
        - debugging
        - monitoring
        - analytics

    dépendront tous des logs.

    IMPORTANT :
    -------------
    Tous les modules utiliseront CETTE classe.
    """

    # =====================================================
    # SINK JSON STRUCTURÉ (NOUVEAU — observabilité)
    # =====================================================
    # Toujours un FICHIER séparé, jamais stdout : la doctrine en tête de ce fichier
    # (stdout = protocole, stderr = logs texte) reste intacte. Ce sink est une TROISIÈME
    # sortie, additive, qui ne remplace ni stdout ni stderr.
    _json_sink_path: str = None
    _lock = threading.Lock()

    @staticmethod
    def configure_json_sink(path: str):
        """
        Active l'écriture d'événements structurés (JSONL — un objet JSON par ligne) vers
        `path`, en plus des logs texte habituels. À appeler une fois au démarrage (voir
        main.py / Orchestrator.__init__). Tant que cette méthode n'a pas été appelée,
        Logger.event() continue de fonctionner (le résumé texte reste toujours émis sur
        stderr), simplement sans persistance structurée.
        """
        Logger._json_sink_path = path
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        except Exception as e:
            # On ne casse jamais le démarrage du runtime pour un souci d'observabilité
            Logger._log("WARNING", f"[Logger] Impossible de préparer le sink JSON ({path}) : {e}")


    # =====================================================
    # INTERNAL LOG FUNCTION
    # =====================================================

    @staticmethod
    def _log(level: str, message: str):
        """
        Fonction interne commune.

        level :
            niveau du log
            INFO / WARNING / ERROR / DEBUG

        message :
            contenu du log
        """

        # =============================================
        # Génération timestamp
        # =============================================

        timestamp = datetime.now().strftime("%H:%M:%S")

        # =============================================
        # Construction ligne finale
        # =============================================

        final_message = (
            f"[{timestamp}] "
            f"[{level}] "
            f"{message}"
        )

        # =============================================
        # IMPORTANT :
        # Logs envoyés vers stderr
        # =============================================

        print(
            final_message,
            file=sys.stderr,
            flush=True
        )


    # =====================================================
    # INFO
    # =====================================================

    @staticmethod
    def info(message: str):
        """
        Log informatif normal.
        """

        Logger._log("INFO", message)


    # =====================================================
    # WARNING
    # =====================================================

    @staticmethod
    def warning(message: str):
        """
        Warning non critique.
        """

        Logger._log("WARNING", message)


    # =====================================================
    # ERROR
    # =====================================================

    @staticmethod
    def error(message: str):
        """
        Erreur importante.
        """

        Logger._log("ERROR", message)


    # =====================================================
    # DEBUG
    # =====================================================

    @staticmethod
    def debug(message: str):
        """
        Logs debug détaillés.
        """

        Logger._log("DEBUG", message)

    # =====================================================
    # EVENT (NOUVEAU — observabilité structurée)
    # =====================================================

    @staticmethod
    def event(event_type: str, **fields):
        """
        Émet un événement structuré pour la couche d'observabilité visuelle, en plus
        (et indépendamment) des logs texte classiques — rien n'est retiré, ceci s'ajoute.

        Exemple :
            Logger.event("llm_call", tag="Plan", provider_id="gemini",
                         prompt=prompt, response=response, duration_ms=1234)

        Contrat de robustesse : cette méthode ne doit JAMAIS faire planter l'appelant.
        Une mission ne doit jamais échouer à cause d'un problème d'écriture du fichier
        d'observabilité — toute erreur ici est avalée et loggée en WARNING, pas relancée.
        """
        record = {
            # --- FIX (trouvé en construisant la corrélation temporelle du viewer) : datetime.now()
            # naïf renvoie l'heure LOCALE de la machine (Windows côté utilisateur), alors que
            # PlanAttempt/ExecutionNode utilisent time.time() (toujours UTC). Écart mesuré en
            # test réel : exactement 1h de décalage, rendant toute corrélation temporelle fausse.
            # UTC partout, un seul référentiel d'horloge dans tout le système.
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
        }
        record.update(fields)

        # Résumé texte toujours émis (utile même sans sink JSON configuré)
        try:
            summary = json.dumps(fields, ensure_ascii=False, default=str)
        except Exception:
            summary = str(fields)
        if len(summary) > 300:
            summary = summary[:300] + "…"
        Logger._log("EVENT", f"{event_type} :: {summary}")

        if Logger._json_sink_path:
            try:
                line = json.dumps(record, ensure_ascii=False, default=str)
                with Logger._lock:
                    with open(Logger._json_sink_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            except Exception as e:
                Logger._log("WARNING", f"[Logger] Échec d'écriture du sink JSON (non bloquant) : {e}")
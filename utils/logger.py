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


# datetime :
# -----------
# Sert à générer timestamps/date/heure.
#
from datetime import datetime


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
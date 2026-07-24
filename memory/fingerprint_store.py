"""
memory/fingerprint_store.py
===========================
Stockage des empreintes de missions pour éviter les doublons d'analyse.
"""

import sqlite3
import hashlib
import json
from typing import Optional
from utils.logger import Logger


class FingerprintStore:
    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _initialize_db(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS mission_fingerprints (
                        mission_id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_fingerprint ON mission_fingerprints(fingerprint)')
                conn.commit()
                Logger.debug("[FingerprintStore] Table mission_fingerprints prête.")
        except Exception as e:
            Logger.error(f"[FingerprintStore] Erreur d'initialisation : {e}")

    def compute_fingerprint(self, goal: str, plan: dict, signatures: list) -> str:
        """Calcule une empreinte unique pour une mission."""
        data = {
            "goal": goal,
            "plan": plan,
            "signatures": sorted([f"{s.action}|{s.object}" for s in signatures])
        }
        json_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def exists(self, fingerprint: str) -> bool:
        """Vérifie si une empreinte existe déjà."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM mission_fingerprints WHERE fingerprint = ?", (fingerprint,))
                return cursor.fetchone() is not None
        except Exception as e:
            Logger.error(f"[FingerprintStore] Erreur exists : {e}")
            return False

    def save(self, mission_id: str, fingerprint: str):
        """Enregistre une nouvelle empreinte."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO mission_fingerprints (mission_id, fingerprint) VALUES (?, ?)",
                    (mission_id, fingerprint)
                )
                conn.commit()
                Logger.debug(f"[FingerprintStore] Empreinte sauvegardée : {fingerprint[:16]}...")
        except Exception as e:
            Logger.error(f"[FingerprintStore] Erreur save : {e}")

    def get_by_mission_id(self, mission_id: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT fingerprint FROM mission_fingerprints WHERE mission_id = ?", (mission_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            Logger.error(f"[FingerprintStore] Erreur get_by_mission_id : {e}")
            return None
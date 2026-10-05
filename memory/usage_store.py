"""
memory/usage_store.py
======================
Compteurs tokens persistants (survivent au redémarrage).

Une ligne par mission + cumul global reconstruit par somme.
Même convention que les autres stores : SQLite `memory.db`,
écriture best-effort (jamais de crash appelant).
"""

import sqlite3
from typing import Dict, Any
from utils.logger import Logger


class UsageStore:

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
                    CREATE TABLE IF NOT EXISTS token_usage (
                        mission_id TEXT PRIMARY KEY,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        calls INTEGER NOT NULL DEFAULT 0,
                        real_calls INTEGER NOT NULL DEFAULT 0,
                        estimated_calls INTEGER NOT NULL DEFAULT 0
                    )
                ''')
                conn.commit()
                Logger.info(f"Usage store ready at {self.db_path}")
        except Exception as e:
            Logger.error(f"Usage store initialization failed: {str(e)}")

    def record(self, mission_id: str, usage: Dict[str, Any]) -> None:
        """Ajoute un appel au cumul de la mission (insertion ou addition)."""
        try:
            mid = mission_id or "global"
            p = int(usage.get("prompt_tokens") or 0)
            c = int(usage.get("completion_tokens") or 0)
            t = int(usage.get("total_tokens") or (p + c))
            real = 1 if usage.get("source") == "real" else 0
            estimated = 0 if usage.get("source") == "real" else 1
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO token_usage
                        (mission_id, prompt_tokens, completion_tokens, total_tokens, calls, real_calls, estimated_calls)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(mission_id) DO UPDATE SET
                        prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                        completion_tokens = completion_tokens + excluded.completion_tokens,
                        total_tokens = total_tokens + excluded.total_tokens,
                        calls = calls + 1,
                        real_calls = real_calls + excluded.real_calls,
                        estimated_calls = estimated_calls + excluded.estimated_calls
                ''', (mid, p, c, t, real, estimated))
                conn.commit()
        except Exception as e:
            Logger.error(f"Failed to record usage: {str(e)}")

    def load_all(self) -> Dict[str, Dict[str, int]]:
        """Tout l'usage par mission (pour recharger la RAM au démarrage)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT mission_id, prompt_tokens, completion_tokens,
                           total_tokens, calls, real_calls, estimated_calls
                    FROM token_usage
                ''')
                rows = cursor.fetchall()
                return {
                    row[0]: {
                        "prompt_tokens": row[1], "completion_tokens": row[2],
                        "total_tokens": row[3], "calls": row[4],
                        "real_calls": row[5], "estimated_calls": row[6],
                    }
                    for row in rows
                }
        except Exception as e:
            Logger.error(f"Failed to load usage: {str(e)}")
            return {}

    def clear_all(self) -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM token_usage')
                conn.commit()
        except Exception as e:
            Logger.error(f"Failed to clear usage: {str(e)}")

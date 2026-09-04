# memory/session_store.py
import sqlite3
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from utils.logger import Logger

class SessionStore:
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
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        goal_stack TEXT,              -- JSON : [{"text":"...", "timestamp":"...", "status":"..."}]
                        unresolved_issues TEXT,       -- JSON : ["issue 1", "issue 2"]
                        mission_history TEXT,         -- JSON : ["mission_id_1", "mission_id_2"]
                        mood TEXT,
                        last_mission_status TEXT,
                        last_activity DATETIME,
                        discovery_history TEXT,       -- JSON : ["sig1", "sig2"]   <-- NOUVEAU
                        active_investigation_targets TEXT,  -- JSON : ["last_mission", "abc123"]
                        insights_by_mission TEXT,     -- JSON : {"abc123": [{"question":..., "answer":...}]}
                        asset_registry TEXT,          -- JSON : {"session_id":..., "assets":[...]}
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_activity ON sessions(last_activity)')
                
                # Migration : ajouter les colonnes si elles n'existent pas (bases existantes)
                for col, default in (
                    ("discovery_history", "'[]'"),
                    ("active_investigation_targets", "'[]'"),
                    ("insights_by_mission", "'{}'"),
                    ("asset_registry", "'{}'"),
                ):
                    try:
                        cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT {default}")
                        Logger.info(f"[SessionStore] Migration: colonne '{col}' ajoutée.")
                    except sqlite3.OperationalError:
                        pass  # colonne déjà présente
                
                conn.commit()
                Logger.info("[SessionStore] Table 'sessions' prête.")
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur d'initialisation : {e}")

    def upsert_session(self, session_id: str, context_dict: Dict[str, Any]) -> None:
        """
        Sauvegarde ou met à jour une session.
        context_dict peut contenir : goal_stack, unresolved_issues, mission_history, mood,
        last_mission_status, discovery_history, active_investigation_targets, insights_by_mission, asset_registry.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO sessions (
                        session_id,
                        goal_stack,
                        unresolved_issues,
                        mission_history,
                        mood,
                        last_mission_status,
                        last_activity,
                        discovery_history,
                        active_investigation_targets,
                        insights_by_mission,
                        asset_registry,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    session_id,
                    json.dumps(context_dict.get("goal_stack", []), ensure_ascii=False),
                    json.dumps(context_dict.get("unresolved_issues", []), ensure_ascii=False),
                    json.dumps(context_dict.get("mission_history", []), ensure_ascii=False),
                    context_dict.get("mood"),
                    context_dict.get("last_mission_status"),
                    datetime.now().isoformat(),
                    json.dumps(context_dict.get("discovery_history", []), ensure_ascii=False),
                    json.dumps(context_dict.get("active_investigation_targets", []), ensure_ascii=False),
                    json.dumps(context_dict.get("insights_by_mission", {}), ensure_ascii=False),
                    json.dumps(context_dict.get("asset_registry", {}), ensure_ascii=False),
                ))
                conn.commit()
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur upsert session {session_id} : {e}")

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    d["goal_stack"] = json.loads(d.get("goal_stack") or "[]")
                    d["unresolved_issues"] = json.loads(d.get("unresolved_issues") or "[]")
                    d["mission_history"] = json.loads(d.get("mission_history") or "[]")
                    d["discovery_history"] = json.loads(d.get("discovery_history") or "[]")
                    d["active_investigation_targets"] = json.loads(d.get("active_investigation_targets") or "[]")
                    d["insights_by_mission"] = json.loads(d.get("insights_by_mission") or "{}")
                    d["asset_registry"] = json.loads(d.get("asset_registry") or "{}")
                    return d
                return None
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur get_session {session_id} : {e}")
            return None

    def delete_session(self, session_id: str) -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                Logger.info(f"[SessionStore] Session supprimée : {session_id}")
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur delete_session {session_id} : {e}")

    def get_sessions_count(self) -> int:
        """Retourne le nombre total de sessions dans SQLite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM sessions')
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur comptage sessions : {e}")
            return 0

    def clear_all_sessions(self) -> int:
        """Supprime toutes les sessions de la base SQLite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM sessions')
                count = cursor.rowcount
                conn.commit()
                Logger.info(f"[SessionStore] Toutes les sessions purgées ({count} sessions).")
                return count
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur purge sessions : {e}")
            return 0
    
    def get_recurrent_themes(self, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT scope, COUNT(*) as occurrences
                    FROM lessons
                    WHERE source_episodes_json LIKE ?
                    GROUP BY scope
                    ORDER BY occurrences DESC
                    LIMIT ?
                ''', (f'%"{session_id}"%', limit))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[SessionStore] Erreur get_recurrent_themes : {e}")
            return []

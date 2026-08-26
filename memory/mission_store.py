# memory/mission_store.py
import sqlite3
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from utils.logger import Logger
from memory.session_memory import MissionCache


def _safe_json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


class MissionStore:

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
                    CREATE TABLE IF NOT EXISTS episodes (
                        mission_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        environment TEXT DEFAULT 'simulated',
                        status TEXT NOT NULL,
                        execution_tree_json TEXT NOT NULL,
                        resolved_data_json TEXT NOT NULL,
                        presentator_result_json TEXT NULL,
                        summary TEXT NULL,
                        schema_version INTEGER DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        finished_at DATETIME,
                        analyzed_at DATETIME NULL
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_episodes_analyzed ON episodes(analyzed_at)')

                # --- MIGRATIONS SILENCIEUSES ---
                for col in ['analyzed_at', 'presentator_result_json', 'summary']:
                    try:
                        cursor.execute(f'ALTER TABLE episodes ADD COLUMN {col} TEXT NULL')
                        Logger.info(f"[MissionStore] Migration: colonne '{col}' ajoutée.")
                    except sqlite3.OperationalError:
                        pass  # colonne existe déjà

                conn.commit()
                Logger.info("[MissionStore] Base et table 'episodes' prêtes (avec colonne summary).")
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur d'initialisation : {e}")

    def save_episode(self, mission_cache: MissionCache, session_id: str, environment: str = "simulated") -> None:
        """Sauvegarde complète de l'épisode avec le résumé."""
        try:
            tree_json = json.dumps(
                mission_cache.execution_tree.model_dump(mode='json') if mission_cache.execution_tree else {},
                indent=2, ensure_ascii=False
            )
            resolved_json = json.dumps(mission_cache.resolved_data, indent=2, ensure_ascii=False, default=_safe_json_default)
            presentator_json = json.dumps(
                mission_cache.presentator_result if mission_cache.presentator_result else {},
                ensure_ascii=False,
                default=_safe_json_default
            )
            finished_at_iso = mission_cache.finished_at.isoformat() if mission_cache.finished_at else None
            summary = mission_cache.summary or ""

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO episodes (
                        mission_id, session_id, goal, environment, status,
                        execution_tree_json, resolved_data_json, presentator_result_json,
                        summary, schema_version, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    mission_cache.mission_id, session_id, mission_cache.goal,
                    environment, mission_cache.status, tree_json, resolved_json, presentator_json,
                    summary, 1, finished_at_iso
                ))
                conn.commit()
                Logger.info(f"[MissionStore] 💾 Épisode sauvegardé (avec résumé) : {mission_cache.mission_id}")
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur sauvegarde {mission_cache.mission_id} : {e}")

    def get_unanalyzed_episodes(self) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM episodes
                    WHERE analyzed_at IS NULL
                      AND (
                            (execution_tree_json IS NOT NULL AND execution_tree_json != '{}')
                         OR (presentator_result_json IS NOT NULL AND presentator_result_json NOT IN ('{}', 'null', ''))
                      )
                ''')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur get_unanalyzed_episodes : {e}")
            return []

    def update_presentator_result(self, mission_id: str, presentator_result: Dict[str, Any]) -> None:
        """Mise à jour ciblée (gardée pour compatibilité, mais plus utilisée dans le flux principal)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE episodes SET presentator_result_json = ? WHERE mission_id = ?",
                    (json.dumps(presentator_result, ensure_ascii=False), mission_id)
                )
                conn.commit()
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur update_presentator_result {mission_id} : {e}")

    def reset_analyzed(self, mission_ids: Optional[List[str]] = None) -> int:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if mission_ids:
                    placeholders = ",".join("?" for _ in mission_ids)
                    cursor.execute(
                        f"UPDATE episodes SET analyzed_at = NULL WHERE mission_id IN ({placeholders})",
                        tuple(mission_ids)
                    )
                else:
                    cursor.execute("UPDATE episodes SET analyzed_at = NULL")
                affected = cursor.rowcount
                conn.commit()
                Logger.info(f"[MissionStore] 🔄 Ré-analyse forcée : {affected} épisode(s) réinitialisé(s).")
                return affected
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur reset_analyzed : {e}")
            return 0

    def mark_analyzed(self, mission_id: str) -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE episodes SET analyzed_at = datetime('now') WHERE mission_id = ?",
                    (mission_id,)
                )
                conn.commit()
                Logger.info(f"[MissionStore] ✅ Épisode marqué analysé : {mission_id}")
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur mark_analyzed {mission_id} : {e}")

    def get_episode(self, mission_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM episodes WHERE mission_id = ?', (mission_id,))
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    try:
                        raw_tree = d.pop("execution_tree_json", "{}") or "{}"
                        d["execution_tree"] = json.loads(raw_tree) if raw_tree and raw_tree != "{}" else None
                    except Exception as e:
                        Logger.warning(f"[MissionStore] Échec du parsing execution_tree pour {mission_id} : {e}")
                        d["execution_tree"] = None
                    try:
                        raw_data = d.pop("resolved_data_json", "{}") or "{}"
                        d["resolved_data"] = json.loads(raw_data) if raw_data and raw_data != "{}" else {}
                    except Exception as e:
                        Logger.warning(f"[MissionStore] Échec du parsing resolved_data pour {mission_id} : {e}")
                        d["resolved_data"] = {}
                    try:
                        raw_pres = d.pop("presentator_result_json", "{}") or "{}"
                        d["presentator_result"] = json.loads(raw_pres) if raw_pres and raw_pres != "{}" else None
                    except Exception:
                        d["presentator_result"] = None
                    return d
                return None
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur de lecture épisode {mission_id} : {e}")
            return None            
    def get_episodes_by_session(self, session_id: str) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM episodes WHERE session_id = ? ORDER BY created_at DESC', (session_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[MissionStore] Erreur de lecture des épisodes pour session {session_id} : {e}")
            return []

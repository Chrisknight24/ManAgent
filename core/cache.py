# core/cache.py
# Version avec invalidation fine par signatures

import json
import hashlib
import sqlite3
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from utils.logger import Logger
from core.constants import CACHE_MAX_ENTRIES, CACHE_TTL_SECONDS

class CacheManager:
    """
    Gestionnaire de cache persistant.
    Stocke les résultats de retrieval, compactor et advisor dans une table SQLite.
    """

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._initialize_table()
        self.max_entries = CACHE_MAX_ENTRIES
        self.ttl_seconds = CACHE_TTL_SECONDS

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _initialize_table(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        cache_key TEXT PRIMARY KEY,
                        cache_type TEXT NOT NULL,
                        value TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        invalidation_markers TEXT,  -- JSON array
                        version INTEGER DEFAULT 1
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_cache_type ON cache_entries(cache_type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_used_at ON cache_entries(last_used_at)')
                conn.commit()
                Logger.debug("[CacheManager] Table cache_entries prête.")
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur d'initialisation : {e}")

    def _compute_key(self, params: Dict[str, Any]) -> str:
        sorted_params = json.dumps(params, sort_keys=True)
        return hashlib.sha256(sorted_params.encode()).hexdigest()

    def _normalize_signatures(self, signatures: List[Dict[str, str]]) -> List[str]:
        if not signatures:
            return []
        normalized = []
        for s in signatures:
            action = s.get("action", "").strip().lower()
            obj = s.get("object", "").strip().lower()
            if action and obj:
                normalized.append(f"{action}|{obj}")
        return sorted(normalized)

    async def get(
        self,
        cache_type: str,
        params: Dict[str, Any],
        max_age_seconds: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._compute_key(params)

        def _query():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT value, created_at FROM cache_entries WHERE cache_key = ? AND cache_type = ?"
                args = [key, cache_type]

                if max_age_seconds is not None:
                    cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
                    query += " AND created_at >= ?"
                    args.append(cutoff.isoformat())

                cursor.execute(query, args)
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "UPDATE cache_entries SET last_used_at = CURRENT_TIMESTAMP WHERE cache_key = ?",
                        (key,)
                    )
                    conn.commit()
                    return json.loads(row[0])
                return None

        try:
            result = await asyncio.to_thread(_query)
            if result:
                Logger.debug(f"[CacheManager] Cache hit : {cache_type} - {key[:16]}...")
            else:
                Logger.debug(f"[CacheManager] Cache miss : {cache_type} - {key[:16]}...")
            return result
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur get : {e}")
            return None

    async def set(
        self,
        cache_type: str,
        params: Dict[str, Any],
        value: Dict[str, Any],
        invalidation_markers: Optional[List[str]] = None
    ) -> None:
        key = self._compute_key(params)
        markers_json = json.dumps(invalidation_markers or [])

        def _insert():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO cache_entries
                    (cache_key, cache_type, value, invalidation_markers, last_used_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (key, cache_type, json.dumps(value), markers_json))
                conn.commit()

        try:
            await asyncio.to_thread(_insert)
            Logger.debug(f"[CacheManager] Cache mis à jour : {cache_type} - {key[:16]}...")

            if self.max_entries > 0:
                count = self._count_entries()
                if count > self.max_entries:
                    await self.cleanup(self.max_entries)
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur set : {e}")

    async def invalidate(self, markers: List[str]) -> int:
        if not markers:
            return 0

        markers_json = json.dumps(markers)

        def _delete():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT cache_key, invalidation_markers FROM cache_entries")
                rows = cursor.fetchall()
                keys_to_delete = []
                for key, markers_str in rows:
                    if not markers_str:
                        continue
                    try:
                        stored_markers = json.loads(markers_str)
                        if any(m in stored_markers for m in markers):
                            keys_to_delete.append(key)
                    except Exception:
                        continue
                if keys_to_delete:
                    placeholders = ','.join(['?'] * len(keys_to_delete))
                    cursor.execute(f"DELETE FROM cache_entries WHERE cache_key IN ({placeholders})", keys_to_delete)
                    conn.commit()
                    return len(keys_to_delete)
                return 0

        try:
            count = await asyncio.to_thread(_delete)
            if count > 0:
                Logger.debug(f"[CacheManager] Cache invalidé : {count} entrée(s) supprimée(s)")
            return count
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur invalidate : {e}")
            return 0

    # --- NOUVEAU : Invalidation fine par signatures (pour le MissionCompactor) ---
    async def invalidate_by_signatures(self, signatures: List[Dict[str, str]]) -> int:
        """
        Invalide les entrées du cache du MissionCompactor dont les marqueurs
        correspondent aux signatures fournies.
        signatures : liste de dict avec 'action' et 'object' (peut aussi contenir 'desired_state').
        """
        if not signatures:
            return 0
        markers = self._normalize_signatures(signatures)
        if not markers:
            return 0

        def _delete():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # On ne cible que les entrées de type 'compactor'
                cursor.execute("SELECT cache_key, invalidation_markers FROM cache_entries WHERE cache_type = 'compactor'")
                rows = cursor.fetchall()
                keys_to_delete = []
                for key, markers_str in rows:
                    if not markers_str:
                        continue
                    try:
                        stored_markers = json.loads(markers_str)
                        # Intersection entre les marqueurs stockés et ceux qu'on veut invalider
                        if any(m in stored_markers for m in markers):
                            keys_to_delete.append(key)
                    except Exception:
                        continue
                if keys_to_delete:
                    placeholders = ','.join(['?'] * len(keys_to_delete))
                    cursor.execute(f"DELETE FROM cache_entries WHERE cache_key IN ({placeholders})", keys_to_delete)
                    conn.commit()
                    return len(keys_to_delete)
                return 0

        try:
            count = await asyncio.to_thread(_delete)
            if count > 0:
                Logger.info(f"[CacheManager] Invalidation fine du MissionCompactor : {count} entrée(s) supprimée(s) pour {len(markers)} signatures.")
            else:
                Logger.debug(f"[CacheManager] Aucune entrée du MissionCompactor à invalider pour les signatures fournies.")
            return count
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur invalidate_by_signatures : {e}")
            return 0

    def set_max_entries(self, max_entries: int):
        self.max_entries = max_entries

    def set_ttl(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds

    def _count_entries(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cache_entries")
            return cursor.fetchone()[0]

    async def cleanup(self, max_entries: int = 1000) -> int:
        def _clean():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                count = cursor.fetchone()[0]
                if count <= max_entries:
                    return 0
                to_delete = count - max_entries
                cursor.execute('''
                    DELETE FROM cache_entries
                    WHERE cache_key IN (
                        SELECT cache_key FROM cache_entries
                        ORDER BY last_used_at ASC
                        LIMIT ?
                    )
                ''', (to_delete,))
                conn.commit()
                return to_delete

        try:
            deleted = await asyncio.to_thread(_clean)
            if deleted > 0:
                Logger.info(f"[CacheManager] Nettoyage effectué : {deleted} entrée(s) supprimée(s)")
            return deleted
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur cleanup : {e}")
            return 0

    async def clear_all(self) -> int:
        """Purger l'intégralité du cache."""
        def _clear():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM cache_entries")
                count = cursor.rowcount
                conn.commit()
                return count

        try:
            deleted = await asyncio.to_thread(_clear)
            Logger.info(f"[CacheManager] Cache entièrement vidé ({deleted} entrées supprimées).")
            return deleted
        except Exception as e:
            Logger.error(f"[CacheManager] Erreur purge complète : {e}")
            return 0


"""
memory/mission_profile_store.py
================================
Store pour les MissionProfiles (index vectoriel des missions simples & récurrences).

Utilise sqlite-vec pour le retrieval vectoriel.
Stocke les embeddings en BLOB (format binaire).
Gère également les métriques de récurrence et succès consécutifs pour le Skill Engine (Phase 5).
"""

import sqlite3
import json
import array
import os
import time
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from utils.logger import Logger

# Module importé pour la constante DEFAULT_MODEL
try:
    from core.embedding_service import EmbeddingService
    DEFAULT_EMBEDDING_MODEL = getattr(EmbeddingService, "DEFAULT_MODEL", 'sentence-transformers/all-MiniLM-L6-v2')
except Exception:
    DEFAULT_EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'

TABLE_NAME = "mission_profiles"
VEC_TABLE_NAME = "vec_mission_profiles"
VECTOR_DIM = 384  # Dimension par défaut (all-MiniLM-L6-v2)


class MissionProfileStore:
    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._dll_path = None  # Chemin de la DLL, détecté une seule fois
        self._initialize_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _detect_dll_path(self) -> Optional[str]:
        """Détecte le chemin de vec0.dll une seule fois."""
        if self._dll_path is not None:
            return self._dll_path

        current_dir = os.path.dirname(os.path.abspath(__file__))  # memory/
        root_dir = os.path.dirname(current_dir)  # universal_agent_runtime/
        possible_paths = [
            os.path.join(root_dir, "vec0.dll"),
            os.path.join(current_dir, "vec0.dll"),
            os.path.join(root_dir, "sqlite-vec.dll"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                self._dll_path = p
                return p
        # Si pas trouvé, on essaie juste "vec0" (peut-être dans PATH)
        self._dll_path = "vec0"  # fallback
        return self._dll_path

    def _ensure_extension_loaded(self, conn: sqlite3.Connection) -> None:
        """Charge l'extension sqlite-vec si elle n'est pas déjà chargée."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT vec_version()")
            return  # déjà chargée
        except sqlite3.OperationalError:
            pass

        conn.enable_load_extension(True)
        dll = self._detect_dll_path()
        dll_sql = dll.replace("\\", "\\\\")
        try:
            conn.execute(f"SELECT load_extension('{dll_sql}')")
            Logger.info(f"[MissionProfileStore] Extension chargée depuis {dll}")
        except Exception as e:
            Logger.warning(f"[MissionProfileStore] Extension sqlite-vec non chargée ({e})")

    def _initialize_db(self):
        """Crée la table mission_profiles et l'index vectoriel sqlite-vec.
           Gère aussi les migrations pour les colonnes de récurrence Skill Engine.
        """
        try:
            with self._get_connection() as conn:
                try:
                    self._ensure_extension_loaded(conn)
                except Exception:
                    pass

                cursor = conn.cursor()

                # Table principale
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mission_id TEXT,
                        signature_text TEXT,
                        signature_hash TEXT,
                        action TEXT,
                        object TEXT,
                        desired_state TEXT,
                        embedding BLOB,
                        signature_index INTEGER DEFAULT 0,
                        signature_count INTEGER DEFAULT 1,
                        consecutive_successes INTEGER DEFAULT 0,
                        total_executions INTEGER DEFAULT 0,
                        last_status TEXT,
                        last_updated_at REAL,
                        embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2',
                        embedding_dimension INTEGER DEFAULT 384,
                        root_mission_id TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_mission ON {TABLE_NAME}(mission_id)")
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_sig ON {TABLE_NAME}(signature_text)")

                # Migrations pour tables existantes
                for col_def in [
                    "embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2'",
                    "embedding_dimension INTEGER DEFAULT 384",
                    "root_mission_id TEXT",
                    "signature_hash TEXT",
                    "consecutive_successes INTEGER DEFAULT 0",
                    "total_executions INTEGER DEFAULT 0",
                    "last_status TEXT",
                    "last_updated_at REAL"
                ]:
                    try:
                        cursor.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {col_def}")
                    except sqlite3.OperationalError:
                        pass  # Colonne existe déjà

                # Index sur signature_hash créé APPRÈS la migration de la colonne
                try:
                    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_sighash ON {TABLE_NAME}(signature_hash)")
                except Exception:
                    pass

                # Table virtuelle vectorielle sqlite-vec
                try:
                    cursor.execute(f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE_NAME} USING vec0(
                            embedding float[{VECTOR_DIM}]
                        )
                    """)
                except Exception as ve:
                    Logger.debug(f"[MissionProfileStore] Table vectorielle virtuelles non créée (extension optionnelle) : {ve}")

                conn.commit()
                Logger.info("[MissionProfileStore] Tables prêtes (mission_profiles + index vectoriel & récurrences).")
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur d'initialisation : {e}")

    # =========================================================================
    # PHASE 5 : SUIVI DES RÉCURRENCES & SUCCÈS CONSÉCUTIFS (SKILL ENGINE)
    # =========================================================================

    @staticmethod
    def compute_hash(action: str, object_name: str) -> str:
        from core.plan_models import clean_signature_str
        act = clean_signature_str(action)
        obj = clean_signature_str(object_name)
        return f"sig:{act}:{obj}"

    def record_execution_result_vectorial(
        self,
        signature_text: str,
        embedding: List[float],
        is_success: bool,
        action: Optional[str] = None,
        object_name: Optional[str] = None,
        desired_state: Optional[str] = None
    ) -> Tuple[int, int]:
        """
        Incrémente consecutive_successes en utilisant le matching vectoriel (sqlite-vec).
        Retourne (canonical_profile_id, consecutive_successes).
        """
        from core.plan_models import clean_signature_str
        now = time.time()
        canonical_profile_id = None
        
        clean_act = clean_signature_str(action) if action else ""
        clean_obj = clean_signature_str(object_name) if object_name else ""
        clean_state = clean_signature_str(desired_state) if desired_state else ""
        sig_hash = self.compute_hash(clean_act, clean_obj) if clean_act and clean_obj else None

        # 1. Tenter la recherche vectorielle avec un seuil de similarité plus souple (distance <= 0.35 -> similarity >= 0.65)
        try:
            similar = self.get_similar_profiles(query_embedding=embedding, top_k=1, threshold=0.35)
            if similar:
                canonical_profile_id = similar[0]["id"]
                Logger.debug(f"[MissionProfileStore] Matching vectoriel réussi (sim={similar[0]['similarity']:.2f}) -> Profil ID {canonical_profile_id}")
        except Exception as e:
            Logger.debug(f"[MissionProfileStore] Matching vectoriel échoué ou non disponible : {e}")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 2. Si pas trouvé par vecteur, chercher par hash ou action/object exact normalisé
                if canonical_profile_id is None and sig_hash:
                    cursor.execute(
                        f"SELECT id FROM {TABLE_NAME} WHERE signature_hash = ? OR (LOWER(action) = ? AND LOWER(object) = ?)",
                        (sig_hash, clean_act, clean_obj)
                    )
                    row = cursor.fetchone()
                    if row:
                        canonical_profile_id = row[0]

                if canonical_profile_id is not None:
                    # UPDATE du profil existant
                    cursor.execute(
                        f"SELECT consecutive_successes, total_executions FROM {TABLE_NAME} WHERE id = ?",
                        (canonical_profile_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        prev_successes, total_execs = row
                        new_successes = (prev_successes + 1) if is_success else 0
                        new_total = (total_execs or 0) + 1
                        status_str = "success" if is_success else "failed"

                        cursor.execute(f"""
                            UPDATE {TABLE_NAME}
                            SET consecutive_successes = ?, total_executions = ?, last_status = ?, last_updated_at = ?
                            WHERE id = ?
                        """, (new_successes, new_total, status_str, now, canonical_profile_id))
                        conn.commit()
                        return (canonical_profile_id, new_successes)

                # 3. Si aucun profil existant, on l'insère (utilise insert_profile pour gérer le BLOB)
                profile_id = self.insert_profile(
                    mission_id=f"sig_{now}", # Placeholder
                    signature_text=signature_text,
                    embedding=embedding,
                    action=clean_act,
                    object=clean_obj,
                    desired_state=clean_state
                )
                
                new_successes = 1 if is_success else 0
                status_str = "success" if is_success else "failed"
                cursor.execute(f"""
                    UPDATE {TABLE_NAME}
                    SET consecutive_successes = ?, total_executions = 1, last_status = ?, last_updated_at = ?, signature_hash = ?
                    WHERE id = ?
                """, (new_successes, status_str, now, sig_hash, profile_id))
                conn.commit()
                return (profile_id, new_successes)
                
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur record_execution_result_vectorial : {e}")
            return (-1, 1 if is_success else 0)

    def record_execution_result(
        self,
        action: str,
        object_name: str,
        is_success: bool,
        desired_state: Optional[str] = None
    ) -> int:
        """
        Incrémente consecutive_successes si succès, réinitialise à 0 si échec.
        Retourne le nouveau nombre de succès consécutifs.
        """
        sig_hash = self.compute_hash(action, object_name)
        now = time.time()

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT id, consecutive_successes, total_executions FROM {TABLE_NAME} WHERE signature_hash = ? OR (action = ? AND object = ?)",
                    (sig_hash, action.strip().lower(), object_name.strip().lower())
                )
                row = cursor.fetchone()

                if row:
                    p_id, prev_successes, total_execs = row
                    new_successes = (prev_successes + 1) if is_success else 0
                    new_total = (total_execs or 0) + 1
                    status_str = "success" if is_success else "failed"

                    cursor.execute(f"""
                        UPDATE {TABLE_NAME}
                        SET consecutive_successes = ?, total_executions = ?, last_status = ?, last_updated_at = ?, signature_hash = ?
                        WHERE id = ?
                    """, (new_successes, new_total, status_str, now, sig_hash, p_id))
                else:
                    new_successes = 1 if is_success else 0
                    status_str = "success" if is_success else "failed"
                    sig_text = f"{action} {object_name}"
                    cursor.execute(f"""
                        INSERT INTO {TABLE_NAME}
                        (signature_hash, signature_text, action, object, desired_state, consecutive_successes, total_executions, last_status, last_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """, (sig_hash, sig_text, action.strip().lower(), object_name.strip().lower(), desired_state, new_successes, status_str, now))

                conn.commit()
                return new_successes
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur record_execution_result : {e}")
            return 1 if is_success else 0

    def get_consecutive_successes(self, action: str, object_name: str) -> int:
        sig_hash = self.compute_hash(action, object_name)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT consecutive_successes FROM {TABLE_NAME} WHERE signature_hash = ? OR (action = ? AND object = ?)",
                    (sig_hash, action.strip().lower(), object_name.strip().lower())
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] is not None else 0
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_consecutive_successes : {e}")
            return 0

    # =========================================================================
    # RECHERCHE VECTORIELLE & GESTION DES EMBEDDINGS (ORIGINAL STORE)
    # =========================================================================

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        """Convertit une liste de floats en BLOB binaire (format float32 little-endian)."""
        return array.array('f', embedding).tobytes()

    def _deserialize_embedding(self, blob: bytes) -> List[float]:
        """Convertit un BLOB en liste de floats."""
        return list(array.array('f', blob))

    def insert_profile(
        self,
        mission_id: str,
        signature_text: str,
        embedding: List[float],
        action: Optional[str] = None,
        object: Optional[str] = None,
        desired_state: Optional[str] = None,
        signature_index: int = 0,
        signature_count: int = 1,
        embedding_model: Optional[str] = None,
        embedding_dimension: Optional[int] = None,
        root_mission_id: Optional[str] = None
    ) -> int:
        """
        Insère un MissionProfile vectoriel.
        """
        if embedding_model is None:
            embedding_model = DEFAULT_EMBEDDING_MODEL
        if embedding_dimension is None:
            embedding_dimension = 384

        sig_hash = self.compute_hash(action or "", object or "") if action and object else None

        try:
            with self._get_connection() as conn:
                try:
                    self._ensure_extension_loaded(conn)
                except Exception:
                    pass
                cursor = conn.cursor()

                root_id = root_mission_id if root_mission_id is not None else mission_id

                cursor.execute(f"""
                    INSERT INTO {TABLE_NAME}
                    (mission_id, signature_text, signature_hash, action, object, desired_state,
                    embedding, signature_index, signature_count,
                    embedding_model, embedding_dimension, root_mission_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mission_id, signature_text, sig_hash, action, object, desired_state,
                    self._serialize_embedding(embedding),
                    signature_index, signature_count,
                    embedding_model, embedding_dimension, root_id))
                profile_id = cursor.lastrowid

                # Insertion dans la table virtuelle vectorielle si elle existe
                try:
                    cursor.execute(f"""
                        INSERT INTO {VEC_TABLE_NAME} (rowid, embedding)
                        VALUES (?, ?)
                    """, (profile_id, self._serialize_embedding(embedding)))
                except Exception:
                    pass

                conn.commit()
                Logger.debug(f"[MissionProfileStore] Profile inséré : {mission_id} / {signature_text} (root={root_id})")
                return profile_id
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur insert_profile : {e}")
            raise

    def get_similar_profiles(
        self,
        query_embedding: List[float],
        top_k: int = 20,
        threshold: float = 0.0,
        embedding_model: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Recherche vectorielle via sqlite-vec."""
        try:
            with self._get_connection() as conn:
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()
                query_blob = self._serialize_embedding(query_embedding)

                sql = f"""
                    SELECT
                        p.id,
                        p.mission_id,
                        p.signature_text,
                        p.action,
                        p.object,
                        p.desired_state,
                        p.embedding_model,
                        p.embedding_dimension,
                        p.root_mission_id,
                        v.distance
                    FROM {VEC_TABLE_NAME} v
                    JOIN {TABLE_NAME} p ON p.id = v.rowid
                    WHERE v.embedding MATCH ? AND k = ?
                """
                params = [query_blob, top_k]

                if embedding_model:
                    sql += " AND p.embedding_model = ?"
                    params.append(embedding_model)

                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    distance = row[9]
                    if threshold > 0 and distance > threshold:
                        continue
                    similarity = 1.0 - distance
                    results.append({
                        "id": row[0],
                        "mission_id": row[1],
                        "signature_text": row[2],
                        "action": row[3],
                        "object": row[4],
                        "desired_state": row[5],
                        "embedding_model": row[6],
                        "embedding_dimension": row[7],
                        "root_mission_id": row[8],
                        "distance": distance,
                        "similarity": similarity
                    })
                return results
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_similar_profiles : {e}")
            return []

    def get_profiles_by_mission(self, mission_id: str) -> List[Dict[str, Any]]:
        """Retourne tous les profils associés à une mission donnée."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT id, mission_id, signature_text, action, object, desired_state,
                        signature_index, signature_count,
                        embedding_model, embedding_dimension, root_mission_id,
                        created_at
                    FROM {TABLE_NAME}
                    WHERE mission_id = ?
                    ORDER BY signature_index ASC
                """, (mission_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_profiles_by_mission : {e}")
            return []

    def delete_profiles_by_mission(self, mission_id: str) -> int:
        """Supprime tous les profils d'une mission donnée (et leurs entrées vectorielles)."""
        try:
            with self._get_connection() as conn:
                try:
                    self._ensure_extension_loaded(conn)
                except Exception:
                    pass
                cursor = conn.cursor()
                cursor.execute(f"SELECT id FROM {TABLE_NAME} WHERE mission_id = ?", (mission_id,))
                ids = [row[0] for row in cursor.fetchall()]
                if not ids:
                    return 0

                placeholders = ",".join("?" for _ in ids)
                try:
                    cursor.execute(f"DELETE FROM {VEC_TABLE_NAME} WHERE rowid IN ({placeholders})", ids)
                except Exception:
                    pass

                cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE mission_id = ?", (mission_id,))
                conn.commit()
                Logger.info(f"[MissionProfileStore] Profils supprimés pour la mission {mission_id} ({len(ids)} entrées).")
                return len(ids)
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur delete_profiles_by_mission : {e}")
            return 0

    def get_all_profiles(self) -> List[Dict[str, Any]]:
        """Retourne tous les profils (utile pour déboguer ou migration)."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT id, mission_id, signature_text, action, object, desired_state,
                           signature_index, signature_count,
                           embedding_model, embedding_dimension,
                           created_at
                    FROM {TABLE_NAME}
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_all_profiles : {e}")
            return []

    def clear_all_profiles(self) -> int:
        """Supprime tous les profils de mission et réinitialise l'index vectoriel."""
        try:
            with self._get_connection() as conn:
                try:
                    self._ensure_extension_loaded(conn)
                except Exception:
                    pass
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {TABLE_NAME}")
                count = cursor.rowcount
                try:
                    cursor.execute(f"DELETE FROM {VEC_TABLE_NAME}")
                except Exception:
                    pass
                conn.commit()
                Logger.info(f"[MissionProfileStore] Base de profils purgée ({count} profils supprimés).")
                return count
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur clear_all_profiles : {e}")
            return 0

    def get_profiles_count(self) -> int:
        """Retourne le nombre total de profils stockés."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_profiles_count : {e}")
            return 0

    def get_known_signatures(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retourne les signatures les plus fréquentes / récurrentes."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT action, object, desired_state, consecutive_successes, total_executions
                    FROM {TABLE_NAME}
                    WHERE action IS NOT NULL AND object IS NOT NULL AND action != '' AND object != ''
                    ORDER BY consecutive_successes DESC, total_executions DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur get_known_signatures : {e}")
            return []




"""
memory/mission_profile_store.py
================================
Store pour les MissionProfiles (index vectoriel des missions simples).

Utilise sqlite-vec pour le retrieval vectoriel.
Stocke les embeddings en BLOB (format binaire).
Ajoute les métadonnées du modèle (embedding_model, embedding_dimension) pour l’évolutivité.
"""

import sqlite3
import json
import array
import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from utils.logger import Logger

# Module importé pour la constante DEFAULT_MODEL
from core.embedding_service import EmbeddingService

TABLE_NAME = "mission_profiles"
VEC_TABLE_NAME = "vec_mission_profiles"
VECTOR_DIM = 384  # Dimension par défaut (all-MiniLM-L6-v2)


class MissionProfileStore:
    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._dll_path = None  # Chemin de la DLL, détecté une seule fois
        self._initialize_db()

    def _get_connection(self):
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
        # On vérifie rapidement si vec_version existe déjà
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT vec_version()")
            return  # déjà chargée
        except sqlite3.OperationalError:
            pass

        # Sinon, on charge
        conn.enable_load_extension(True)
        dll = self._detect_dll_path()
        # SQLite nécessite des doubles backslashes ou des slashes
        dll_sql = dll.replace("\\", "\\\\")
        try:
            conn.execute(f"SELECT load_extension('{dll_sql}')")
            Logger.info(f"[MissionProfileStore] Extension chargée depuis {dll}")
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Échec du chargement de l'extension : {e}")
            raise

    def _initialize_db(self):
        """Crée la table mission_profiles et l'index vectoriel sqlite-vec.
           Ajoute les colonnes embedding_model et embedding_dimension si elles n'existent pas.
        """
        try:
            with self._get_connection() as conn:
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()

                # Table principale
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mission_id TEXT NOT NULL,
                        signature_text TEXT NOT NULL,
                        action TEXT,
                        object TEXT,
                        desired_state TEXT,
                        embedding BLOB NOT NULL,
                        signature_index INTEGER DEFAULT 0,
                        signature_count INTEGER DEFAULT 1,
                        embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2',
                        embedding_dimension INTEGER DEFAULT 384,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (mission_id) REFERENCES episodes(mission_id) ON DELETE CASCADE
                    )
                """)
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_mission ON {TABLE_NAME}(mission_id)")
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_sig ON {TABLE_NAME}(signature_text)")

                # Migration : ajout des colonnes si elles n'existent pas (pour les bases existantes)
                try:
                    cursor.execute("ALTER TABLE mission_profiles ADD COLUMN embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2'")
                except sqlite3.OperationalError:
                    pass  # colonne existe déjà
                try:
                    cursor.execute("ALTER TABLE mission_profiles ADD COLUMN embedding_dimension INTEGER DEFAULT 384")
                except sqlite3.OperationalError:
                    pass

                # Table virtuelle pour l'index vectoriel
                cursor.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE_NAME} USING vec0(
                        embedding float[{VECTOR_DIM}]
                    )
                """)

                conn.commit()
                Logger.info("[MissionProfileStore] Tables prêtes (mission_profiles + index vectoriel).")
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur d'initialisation : {e}")
            raise

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        """Convertit une liste de floats en BLOB binaire (format float32 little-endian)."""
        return array.array('f', embedding).tobytes()

    def _deserialize_embedding(self, blob: bytes) -> List[float]:
        """Convertit un BLOB en liste de floats."""
        return list(array.array('f', blob))

    def insert_profile(self, mission_id: str, signature_text: str, embedding: List[float],
                       action: Optional[str] = None, object: Optional[str] = None,
                       desired_state: Optional[str] = None,
                       signature_index: int = 0, signature_count: int = 1,
                       embedding_model: Optional[str] = None,
                       embedding_dimension: Optional[int] = None) -> int:
        """
        Insère un MissionProfile dans la base et met à jour l'index vectoriel.

        Si embedding_model ou embedding_dimension ne sont pas fournis, on utilise
        les valeurs par défaut du service d'embedding.
        """
        # Valeurs par défaut si non spécifiées
        if embedding_model is None:
            embedding_model = EmbeddingService.DEFAULT_MODEL
        if embedding_dimension is None:
            embedding_dimension = 384  # dimension par défaut

        try:
            with self._get_connection() as conn:
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()

                cursor.execute(f"""
                    INSERT INTO {TABLE_NAME}
                    (mission_id, signature_text, action, object, desired_state,
                     embedding, signature_index, signature_count,
                     embedding_model, embedding_dimension)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mission_id, signature_text, action, object, desired_state,
                      self._serialize_embedding(embedding),
                      signature_index, signature_count,
                      embedding_model, embedding_dimension))
                profile_id = cursor.lastrowid

                cursor.execute(f"""
                    INSERT INTO {VEC_TABLE_NAME} (rowid, embedding)
                    VALUES (?, ?)
                """, (profile_id, self._serialize_embedding(embedding)))

                conn.commit()
                Logger.debug(f"[MissionProfileStore] Profile inséré : {mission_id} / {signature_text} (id={profile_id}, model={embedding_model})")
                return profile_id
        except Exception as e:
            Logger.error(f"[MissionProfileStore] Erreur insert_profile : {e}")
            raise

    def get_similar_profiles(self, query_embedding: List[float], top_k: int = 20,
                              threshold: float = 0.0, embedding_model: Optional[str] = None) -> List[Dict[str, Any]]:
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
                    distance = row[8]
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
                           embedding_model, embedding_dimension,
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
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()
                cursor.execute(f"SELECT id FROM {TABLE_NAME} WHERE mission_id = ?", (mission_id,))
                ids = [row[0] for row in cursor.fetchall()]
                if not ids:
                    return 0

                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"DELETE FROM {VEC_TABLE_NAME} WHERE rowid IN ({placeholders})", ids)
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
"""
memory/lesson_store.py
=====================================================
PHASE 3 – STOCKAGE DES LEÇONS (KnowledgeBase)
Gère la table 'lessons' dans la base SQLite.
Version mise à jour : ajout de l'embedding vectoriel (sqlite-vec) 
pour la recherche sémantique des leçons (Advisor V2).
"""

import sqlite3
import json
import os
import array
import math
from typing import List, Dict, Any, Optional
from datetime import datetime
from utils.logger import Logger
from core.constants import (
    ENTITY_LEARNER_MIN_EVIDENCE,
    LESSON_STORE_TOP_K,
    LESSON_SIMILARITY_THRESHOLD,
    LESSON_MAX_KEYWORDS_PER_CALL,
    LESSON_MAX_KEYWORDS_TOTAL,
    LESSON_MAX_SOURCE_EPISODES,
)

class LessonStore:

    BASE_VEC_TABLE = "vec_lessons"

    def __init__(self, db_path: str = "memory.db", embedding_model: Optional[str] = None):
        self.db_path = db_path
        self.embedding_model = embedding_model
        self._vec_table = self.BASE_VEC_TABLE
        self._apply_embedding_space()
        self._initialize_db()

    def _apply_embedding_space(self) -> None:
        """Pointe vers les tables de l'espace du modèle (R6 : pas de mélange)."""
        try:
            from embeddings.catalog import vec_table, model_dim
            self._vec_table = vec_table(self.BASE_VEC_TABLE, self.embedding_model)
            self._vector_dim = model_dim(self.embedding_model, default=384)
        except Exception:
            self._vec_table = self.BASE_VEC_TABLE
            self._vector_dim = 384

    def use_embedding_model(self, model_id: Optional[str]) -> str:
        """Bascule l'espace mémoire (hot-swap sûr : anciennes tables préservées)."""
        self.embedding_model = model_id
        self._apply_embedding_space()
        try:
            self._initialize_db()
        except Exception:
            pass
        Logger.info(f"[LessonStore] Espace mémoire : {self._vec_table}")
        return self._vec_table

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _detect_dll_path(self) -> Optional[str]:
        """Détecte le chemin de l'extension sqlite-vec."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(current_dir)
        possible_paths = [
            os.path.join(root_dir, "vec0.dll"),
            os.path.join(current_dir, "vec0.dll"),
            os.path.join(root_dir, "sqlite-vec.dll"),
        ]
        # Dossier de l'exe fige : vec0.dll livre a cote de managent.exe.
        import sys
        if getattr(sys, "frozen", False):
            exe_d = os.path.dirname(os.path.abspath(sys.executable))
            possible_paths.insert(0, os.path.join(exe_d, "vec0.dll"))
        for p in possible_paths:
            if os.path.exists(p):
                return p
        return "vec0"

    def _ensure_extension_loaded(self, conn: sqlite3.Connection) -> None:
        """Charge sqlite-vec si nécessaire."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT vec_version()")
            return
        except sqlite3.OperationalError:
            pass
        conn.enable_load_extension(True)
        dll = self._detect_dll_path().replace("\\", "\\\\")
        try:
            conn.execute(f"SELECT load_extension('{dll}')")
        except Exception as e:
            Logger.warning(f"[LessonStore] Extension sqlite-vec non chargée : {e}")

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        return array.array('f', embedding).tobytes()

    def _ensure_column(self, conn, table: str, column: str, column_def: str) -> bool:
        """Vérifie l'existence d'une colonne et l'ajoute si manquante."""
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if column in columns:
            return True
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
            conn.commit()
            Logger.info(f"[LessonStore] Colonne '{column}' ajoutée à la table {table}.")
            return True
        except Exception as e:
            Logger.error(f"[LessonStore] Échec ajout colonne '{column}' : {e}")
            return False

    def _initialize_db(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS lessons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        recommendation TEXT NOT NULL,
                        confidence REAL DEFAULT 0.5,
                        evidence_count INTEGER DEFAULT 1,
                        environment TEXT DEFAULT 'simulated',
                        contradiction_count INTEGER DEFAULT 0,
                        is_active BOOLEAN DEFAULT 1,
                        keywords_json TEXT DEFAULT '[]',
                        source_episodes_json TEXT DEFAULT '[]',
                        polarity TEXT DEFAULT 'avoid',
                        embedding BLOB,
                        last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_scope ON lessons(scope)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_entity ON lessons(entity_type)')

                # --- MIGRATIONS ---
                self._ensure_column(conn, "lessons", "keywords_json", "TEXT DEFAULT '[]'")
                self._ensure_column(conn, "lessons", "source_episodes_json", "TEXT DEFAULT '[]'")
                self._ensure_column(conn, "lessons", "polarity", "TEXT DEFAULT 'avoid'")
                self._ensure_column(conn, "lessons", "is_consolidated", "BOOLEAN DEFAULT 0")
                self._ensure_column(conn, "lessons", "consolidated_from_id", "INTEGER")
                self._ensure_column(conn, "lessons", "conflict_resolution", "TEXT")
                self._ensure_column(conn, "lessons", "embedding", "BLOB")

                self._ensure_extension_loaded(conn)
                cursor.execute(f'''
                    CREATE VIRTUAL TABLE IF NOT EXISTS {self._vec_table} USING vec0(
                        embedding float[{self._vector_dim}]
                    )
                ''')

                conn.commit()
                Logger.info(f"[LessonStore] Table 'lessons' et '{self._vec_table}' prêtes.")
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur d'initialisation : {e}")

    MAX_KEYWORDS_PER_CALL = LESSON_MAX_KEYWORDS_PER_CALL
    MAX_KEYWORDS_TOTAL = LESSON_MAX_KEYWORDS_TOTAL
    MAX_SOURCE_EPISODES = LESSON_MAX_SOURCE_EPISODES

    def upsert_lesson(self, entity_type: str, scope: str, recommendation: str,
                   environment: str = "simulated", keywords: Optional[List[str]] = None,
                   mission_id: Optional[str] = None, polarity: str = "avoid",
                   embedding: Optional[List[float]] = None) -> None:
        """
        Ajoute une nouvelle leçon brute (ne met pas à jour l'existante).
        Pour la consolidation, on veut des lignes distinctes.
        """
        keywords = (keywords or [])[:self.MAX_KEYWORDS_PER_CALL]
        blob = self._serialize_embedding(embedding) if embedding else None
        try:
            with self._get_connection() as conn:
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()
                # Leçons vivantes : même sujet + même texte (normalisé) = on renforce
                # au lieu de dupliquer (sinon la confiance 0.67 ne bouge jamais).
                # Lissage de Laplace cohérent avec la naissance (2/3) :
                # preuve 2 → 0.75, preuve 3 → 0.80, plafond 0.95.
                norm = " ".join(str(recommendation or "").lower().split())
                cursor.execute(
                    "SELECT id, evidence_count, keywords_json, source_episodes_json, polarity "
                    "FROM lessons WHERE entity_type = ? AND scope = ? AND environment = ? "
                    "AND is_active = 1 ORDER BY id DESC LIMIT 20",
                    (entity_type, scope, environment),
                )
                rows = cursor.fetchall()
                opposite_ids = []
                for row in rows:
                    lid, ev, kw_json, src_json, pol = row[0], row[1], row[2], row[3], row[4]
                    try:
                        existing = cursor.execute(
                            "SELECT recommendation FROM lessons WHERE id = ?", (lid,)
                        ).fetchone()
                    except Exception:
                        existing = None
                    existing_norm = " ".join(str((existing[0] if existing else "") or "").lower().split())
                    if norm and existing_norm == norm and (pol or "avoid") == (polarity or "avoid"):
                        try:
                            merged_kw = sorted(set(json.loads(kw_json or "[]")) | set(keywords))
                        except Exception:
                            merged_kw = sorted(set(keywords))
                        try:
                            sources = json.loads(src_json or "[]")
                        except Exception:
                            sources = []
                        if mission_id and mission_id not in sources:
                            sources.append(mission_id)
                        new_ev = int(ev or 1) + 1
                        new_conf = min(0.95, (new_ev + 1) / (new_ev + 2))
                        cursor.execute(
                            "UPDATE lessons SET evidence_count = ?, confidence = ?, "
                            "keywords_json = ?, source_episodes_json = ?, last_verified_at = ? "
                            "WHERE id = ?",
                            (new_ev, new_conf, json.dumps(merged_kw, ensure_ascii=False),
                             json.dumps(sources[-self.MAX_SOURCE_EPISODES:], ensure_ascii=False),
                             datetime.now().isoformat(), lid),
                        )
                        conn.commit()
                        Logger.debug(f"[LessonStore] Leçon renforcée (id={lid}, preuves={new_ev}, conf={new_conf:.2f}).")
                        return
                    if (pol or "avoid") != (polarity or "avoid"):
                        opposite_ids.append(lid)
                # Preuve contraire (polarité opposée, même sujet) : fait baisser
                # la confiance des leçons opposées au lieu de les laisser à 0.67.
                for oid in opposite_ids[:3]:
                    try:
                        r = cursor.execute(
                            "SELECT evidence_count, contradiction_count FROM lessons WHERE id = ?", (oid,)
                        ).fetchone()
                        if r:
                            ev_o, contra_o = int(r[0] or 1), int(r[1] or 0) + 1
                            conf_o = (ev_o + 1) / (ev_o + contra_o + 2)
                            cursor.execute(
                                "UPDATE lessons SET contradiction_count = ?, confidence = ?, "
                                "last_verified_at = ? WHERE id = ?",
                                (contra_o, conf_o, datetime.now().isoformat(), oid),
                            )
                            Logger.debug(f"[LessonStore] Contradiction enregistrée (id={oid}, conf={conf_o:.2f}).")
                    except Exception:
                        pass
                # Dédup faits sémantiques : même texte (normalisé) = on renforce
                # au lieu de dupliquer (l'UI affichait "user aime" x N).
                if scope == "semantic_fact" and recommendation:
                    norm = " ".join(str(recommendation).lower().split())
                    cursor.execute(
                        "SELECT id, evidence_count, keywords_json FROM lessons "
                        "WHERE scope = ? AND LOWER(TRIM(recommendation)) = ? "
                        "ORDER BY id DESC LIMIT 5",
                        (scope, norm),
                    )
                    for row in cursor.fetchall():
                        lid, ev, kw_json = row[0], row[1], row[2]
                        try:
                            merged = sorted(set(json.loads(kw_json or "[]")) | set(keywords))
                        except Exception:
                            merged = sorted(set(keywords))
                        cursor.execute(
                            "UPDATE lessons SET evidence_count = ?, keywords_json = ?, "
                            "last_verified_at = ? WHERE id = ?",
                            (int(ev or 1) + 1, json.dumps(merged, ensure_ascii=False),
                             datetime.now().isoformat(), lid),
                        )
                        conn.commit()
                        Logger.debug(f"[LessonStore] Fait existant renforcé (id={lid}), pas de doublon.")
                        return
                initial_confidence = 2 / 3
                sources = [mission_id] if mission_id else []
                cursor.execute('''
                    INSERT INTO lessons (
                        entity_type, scope, recommendation, environment,
                        confidence, evidence_count, keywords_json,
                        source_episodes_json, polarity,
                        is_consolidated, is_active, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
                ''', (entity_type, scope, recommendation, environment,
                    initial_confidence, 1,
                    json.dumps(sorted(set(keywords)), ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    polarity, blob))
                
                lesson_id = cursor.lastrowid
                if blob:
                    cursor.execute(f"INSERT INTO {self._vec_table}(rowid, embedding) VALUES (?, ?)", (lesson_id, blob))
                conn.commit()
                Logger.debug(f"[LessonStore] Nouvelle leçon (brute) : {scope} (polarity={polarity})")
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur upsert : {e}")
            
    def add_contradiction(self, entity_type: str, scope: str, environment: str = "simulated") -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, evidence_count, contradiction_count FROM lessons WHERE entity_type = ? AND scope = ? AND environment = ?",
                    (entity_type, scope, environment)
                )
                row = cursor.fetchone()
                if row:
                    lesson_id, evidence_count, contradiction_count = row
                    new_contradiction = contradiction_count + 1
                    new_confidence = (evidence_count + 1) / (evidence_count + new_contradiction + 2)
                    cursor.execute('''
                        UPDATE lessons
                        SET contradiction_count = ?,
                            confidence = ?,
                            last_verified_at = ?
                        WHERE id = ?
                    ''', (new_contradiction, new_confidence, datetime.now().isoformat(), lesson_id))
                    Logger.debug(f"[LessonStore] Contradiction ajoutée : {scope} (conf={new_confidence:.2f})")
                conn.commit()
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur add_contradiction : {e}")

    def get_active_lessons(self, entity_types: List[str], environment: str, limit: int = 300) -> List[Dict[str, Any]]:
        if not entity_types: return []
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in entity_types)
                cursor.execute(f'''
                    SELECT id, entity_type, scope, recommendation, confidence,
                           evidence_count, contradiction_count, keywords_json, polarity
                    FROM lessons
                    WHERE entity_type IN ({placeholders})
                      AND environment = ?
                      AND is_active = 1
                    ORDER BY confidence DESC, evidence_count DESC
                    LIMIT ?
                ''', (*entity_types, environment, limit))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    try:
                        d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
                    except:
                        d["keywords"] = []
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_active_lessons : {e}")
            return []

    def get_lessons(self, entity_type: str, scope_like: str, min_confidence: float = 0.6,
                    min_evidence: int = 3, environment: str = "simulated") -> List[Dict[str, Any]]:
        # DÉPRÉCIÉ
        return []

    def get_all_episodes_unanalyzed(self) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM episodes WHERE execution_tree_json IS NOT NULL AND execution_tree_json != '{}'")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            return []

    def get_unconsolidated_groups(self) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT entity_type, scope, environment,
                        COUNT(*) as raw_count,
                        SUM(CASE WHEN polarity = 'avoid' THEN 1 ELSE 0 END) as avoid_count,
                        SUM(CASE WHEN polarity = 'prefer' THEN 1 ELSE 0 END) as prefer_count
                    FROM lessons
                    WHERE is_consolidated = 0
                    AND is_active = 1
                    GROUP BY entity_type, scope, environment
                    HAVING COUNT(*) >= ?
                ''', (ENTITY_LEARNER_MIN_EVIDENCE,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_unconsolidated_groups : {e}")
            return []
        
    def get_brute_lessons_by_group(self, entity_type: str, scope: str, environment: str) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, polarity, evidence_count, contradiction_count, confidence,
                        recommendation, keywords_json, source_episodes_json
                    FROM lessons
                    WHERE entity_type = ? AND scope = ? AND environment = ?
                    AND is_consolidated = 0
                    AND is_active = 1
                ''', (entity_type, scope, environment))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    try:
                        d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
                    except: d["keywords"] = []
                    try:
                        d["source_episodes"] = json.loads(d.pop("source_episodes_json") or "[]")
                    except: d["source_episodes"] = []
                    results.append(d)
                return results
        except Exception as e:
            return []

    def get_consolidated_lessons(self, entity_types: List[str], environment: str) -> List[Dict[str, Any]]:
        if not entity_types: return []
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in entity_types)
                cursor.execute(f'''
                    SELECT id, entity_type, scope, recommendation, confidence,
                        evidence_count, contradiction_count, keywords_json, polarity,
                        consolidated_from_id, conflict_resolution
                    FROM lessons
                    WHERE entity_type IN ({placeholders})
                    AND environment = ?
                    AND is_consolidated = 1
                    AND is_active = 1
                    ORDER BY confidence DESC, evidence_count DESC
                ''', (*entity_types, environment))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    try:
                        d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
                    except: d["keywords"] = []
                    results.append(d)
                return results
        except Exception as e:
            return []

    def create_consolidated_lesson(self, entity_type: str, scope: str, environment: str,
                                    from_lesson_id: int, recommendation: str,
                                    confidence: float, evidence_count: int,
                                    contradiction_count: int, keywords: List[str],
                                    polarity: str, source_episodes: List[str],
                                    conflict_resolution: Optional[str] = None,
                                    embedding: Optional[List[float]] = None) -> int:
        blob = self._serialize_embedding(embedding) if embedding else None
        try:
            with self._get_connection() as conn:
                self._ensure_extension_loaded(conn)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO lessons (
                        entity_type, scope, environment, is_consolidated,
                        consolidated_from_id, recommendation, confidence,
                        evidence_count, contradiction_count, keywords_json,
                        source_episodes_json, polarity, conflict_resolution, embedding
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    entity_type, scope, environment,
                    from_lesson_id, recommendation, confidence,
                    evidence_count, contradiction_count,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(source_episodes, ensure_ascii=False),
                    polarity, conflict_resolution, blob
                ))
                new_id = cursor.lastrowid
                if blob:
                    cursor.execute(f"INSERT INTO {self._vec_table}(rowid, embedding) VALUES (?, ?)", (new_id, blob))
                conn.commit()
                Logger.info(f"[LessonStore] Leçon consolidée créée : id={new_id}, scope={scope}")
                return new_id
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur create_consolidated_lesson : {e}")
            return 0

    def _deserialize_embedding(self, blob: bytes) -> List[float]:
        if not blob:
            return []
        try:
            a = array.array('f')
            a.frombytes(blob)
            return a.tolist()
        except Exception:
            return []

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def get_similar_lessons(self, query_embedding: List[float], entity_types: List[str], environment: str, top_k: int = LESSON_STORE_TOP_K, include_semantic_facts: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        Recherche vectorielle des leçons les plus pertinentes.
        Si include_semantic_facts est None, il est automatiquement True si 'Orchestrator' fait partie des entity_types.
        Pour le Planner/Solver/Executor, include_semantic_facts est False, ce qui évite la pollution sémantique.
        Applique un score combiné (similarité vectorielle + récence temporelle) pour résoudre les conflits.
        """
        if not entity_types:
            return []

        if include_semantic_facts is None:
            include_semantic_facts = "Orchestrator" in entity_types

        # Si faits sémantiques inclus, on cherche aussi dans Global et Orchestrator
        if include_semantic_facts:
            expanded_entities = list(set(entity_types + ["Global", "Orchestrator"]))
        else:
            expanded_entities = list(set(entity_types))

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                placeholders = ",".join("?" for _ in expanded_entities)
                
                if include_semantic_facts:
                    sql = f"""
                        SELECT id, entity_type, scope, recommendation, confidence,
                               evidence_count, contradiction_count, keywords_json, polarity,
                               is_consolidated, environment, embedding, created_at
                        FROM lessons
                        WHERE is_active = 1
                          AND (entity_type IN ({placeholders}) OR scope = 'semantic_fact')
                    """
                    cursor.execute(sql, expanded_entities)
                else:
                    sql = f"""
                        SELECT id, entity_type, scope, recommendation, confidence,
                               evidence_count, contradiction_count, keywords_json, polarity,
                               is_consolidated, environment, embedding, created_at
                        FROM lessons
                        WHERE is_active = 1
                          AND entity_type IN ({placeholders})
                          AND (scope IS NULL OR scope != 'semantic_fact')
                    """
                    cursor.execute(sql, expanded_entities)
                    
                rows = cursor.fetchall()
                
                if not rows:
                    return []
                
                # Pour le calcul de récence incrémentale à l'épreuve des changements d'horloge du PC,
                # on s'appuie sur la clé primaire auto-incrémentée 'id' (strictement monotone).
                max_id = max(row["id"] for row in rows) if rows else 1
                
                scored_results = []
                dim_skipped = 0
                query_dim = len(query_embedding) if query_embedding else 0
                for row in rows:
                    d = dict(row)
                    emb_blob = d.pop("embedding", None)
                    similarity = 0.0
                    lesson_id = d.get("id", 1)

                    if emb_blob:
                        emb_vec = self._deserialize_embedding(emb_blob)
                        # Garde dimension : une leçon écrite par un autre modèle
                        # (ex : avant l'unification) ne doit pas polluer le score.
                        if emb_vec and query_dim and len(emb_vec) != query_dim:
                            dim_skipped += 1
                            continue
                        if emb_vec:
                            similarity = self._cosine_similarity(query_embedding, emb_vec)
                    
                    # Récence basée sur le numéro de séquence auto-incrémenté 'id' (entre 0.1 et 1.0)
                    recency_score = 0.1 + 0.9 * (lesson_id / max_id)

                    # Score global combiné : 60% similarité + 20% récence + 20% confiance.
                    # Sans la confiance, une leçon prouvée et une leçon douteuse
                    # (toutes deux à 0.67) se valaient — le poison passait pareil.
                    try:
                        conf = float(d.get("confidence", 0.67) or 0)
                    except Exception:
                        conf = 0.0
                    conf = max(0.0, min(1.0, conf))
                    combined_score = (similarity * 0.6) + (recency_score * 0.2) + (conf * 0.2)
                    
                    try:
                        d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
                    except Exception:
                        d["keywords"] = []
                    
                    d["similarity"] = similarity
                    d["recency_score"] = recency_score
                    d["combined_score"] = combined_score
                    scored_results.append(d)
                
                # Trier par score combiné décroissant
                scored_results.sort(key=lambda x: (x["combined_score"], x.get("is_consolidated", 0)), reverse=True)
                if dim_skipped:
                    Logger.debug(f"[LessonStore] {dim_skipped} leçon(s) ignorée(s) : dimension incompatible avec la requête ({query_dim}).")
                
                # Conserver les résultats pertinents
                filtered = [r for r in scored_results if r["similarity"] > LESSON_SIMILARITY_THRESHOLD or r.get("scope") == "semantic_fact"]
                if not filtered:
                    filtered = scored_results
                
                return filtered[:top_k]

        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_similar_lessons : {e}")
            return []

    def get_similar_facts(self, query_embedding: List[float], environment: str = "simulated",
                          top_k: int = 3, min_similarity: float = 0.30) -> List[Dict[str, Any]]:
        """Faits connus proches d'un message (pour dire au modèle ce qu'on sait déjà).

        Pur côté décision : tri cosinus, seuil, jamais de fusion ni suppression.
        """
        if not query_embedding:
            return []
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, scope, recommendation, confidence, evidence_count,
                           created_at, last_verified_at
                    FROM lessons
                    WHERE is_active = 1 AND scope = 'semantic_fact' AND environment = ?
                ''', (environment,))
                scored = []
                for row in cursor.fetchall():
                    d = dict(row)
                    emb_blob = None
                    try:
                        emb_blob = cursor.execute(
                            "SELECT embedding FROM lessons WHERE id = ?", (d["id"],)).fetchone()
                        emb_blob = emb_blob[0] if emb_blob else None
                    except Exception:
                        emb_blob = None
                    sim = 0.0
                    if emb_blob:
                        vec = self._deserialize_embedding(emb_blob)
                        if vec:
                            sim = self._cosine_similarity(query_embedding, vec)
                    if sim >= min_similarity:
                        d["similarity"] = sim
                        scored.append(d)
                scored.sort(key=lambda x: x["similarity"], reverse=True)
                return scored[:top_k]
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_similar_facts : {e}")
            return []

    def get_lessons_count(self) -> int:
        """Retourne le nombre total de règles/leçons apprises."""

    def mark_group_consolidated(self, entity_type: str, scope: str, environment: str) -> int:
        """Marque les brutes d'un groupe (classées, jamais effacées).

        Après ça, le groupe ne se regroupe plus, mais chaque ligne reste
        lisible en base et en HTML. Retourne le nombre de lignes marquées.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE lessons SET is_consolidated = 1
                    WHERE entity_type = ? AND scope = ? AND environment = ?
                      AND is_consolidated = 0 AND is_active = 1
                ''', (entity_type, scope, environment))
                conn.commit()
                return cursor.rowcount or 0
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur mark_group_consolidated : {e}")
            return 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM lessons')
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur comptage leçons : {e}")
            return 0

    def get_all_lessons(self) -> List[Dict[str, Any]]:
        """Retourne toutes les leçons actives sans blob d'embedding pour export."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, entity_type, scope, recommendation, confidence, 
                           evidence_count, environment, contradiction_count, is_active, 
                           keywords_json, source_episodes_json, is_consolidated, polarity
                    FROM lessons
                ''')
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    try:
                        d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
                    except Exception:
                        d["keywords"] = []
                    try:
                        d["source_episodes"] = json.loads(d.pop("source_episodes_json") or "[]")
                    except Exception:
                        d["source_episodes"] = []
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_all_lessons : {e}")
            return []

    def clear_all_lessons(self) -> int:
        """Supprime toutes les leçons et retourne le nombre supprimé."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM lessons')
                count = cursor.rowcount
                try:
                    cursor.execute(f'DELETE FROM {self._vec_table}')
                except Exception:
                    pass
                conn.commit()
                Logger.info(f"[LessonStore] Base de leçons purgée ({count} règles supprimées).")
                return count
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur suppression leçons : {e}")
            return 0


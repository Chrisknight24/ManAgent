# memory/lesson_store.py
# =====================================================
# PHASE 3 – STOCKAGE DES LEÇONS (KnowledgeBase)
# Gère la table 'lessons' dans la base SQLite.
# Version corrigée avec vérification des colonnes.
# =====================================================

import sqlite3
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from utils.logger import Logger


class LessonStore:

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

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
                # Création de la table
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
                        last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_scope ON lessons(scope)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_entity ON lessons(entity_type)')

                # --- MIGRATIONS (colonnes ajoutées progressivement) ---
                # On utilise la fonction robuste _ensure_column
                self._ensure_column(conn, "lessons", "keywords_json", "TEXT DEFAULT '[]'")
                self._ensure_column(conn, "lessons", "source_episodes_json", "TEXT DEFAULT '[]'")
                self._ensure_column(conn, "lessons", "polarity", "TEXT DEFAULT 'avoid'")
                self._ensure_column(conn, "lessons", "is_consolidated", "BOOLEAN DEFAULT 0")
                self._ensure_column(conn, "lessons", "consolidated_from_id", "INTEGER")
                self._ensure_column(conn, "lessons", "conflict_resolution", "TEXT")

                conn.commit()
                Logger.info("[LessonStore] Table 'lessons' prête.")
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur d'initialisation : {e}")

    # Plafonds défensifs
    MAX_KEYWORDS_PER_CALL = 6
    MAX_KEYWORDS_TOTAL = 20
    MAX_SOURCE_EPISODES = 50

    # =====================================================
    # UPSERT (Clé = entity_type + scope + environment)
    # =====================================================

    def upsert_lesson(self, entity_type: str, scope: str, recommendation: str,
                   environment: str = "simulated", keywords: Optional[List[str]] = None,
                   mission_id: Optional[str] = None, polarity: str = "avoid") -> None:
        """
        Ajoute une nouvelle leçon brute (ne met pas à jour l'existante).
        Pour la consolidation, on veut des lignes distinctes.
        """
        keywords = (keywords or [])[:self.MAX_KEYWORDS_PER_CALL]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # On insère toujours une nouvelle ligne
                initial_confidence = 2 / 3
                sources = [mission_id] if mission_id else []
                cursor.execute('''
                    INSERT INTO lessons (
                        entity_type, scope, recommendation, environment,
                        confidence, evidence_count, keywords_json,
                        source_episodes_json, polarity,
                        is_consolidated, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
                ''', (entity_type, scope, recommendation, environment,
                    initial_confidence, 1,
                    json.dumps(sorted(set(keywords)), ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    polarity))
                conn.commit()
                Logger.debug(f"[LessonStore] Nouvelle leçon (brute) : {scope} (polarity={polarity}, keywords={keywords})")
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur upsert : {e}")
            
    def add_contradiction(self, entity_type: str, scope: str, environment: str = "simulated") -> None:
        """Incrémente contradiction_count pour une leçon existante."""
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
                else:
                    Logger.warning(f"[LessonStore] Contradiction sur leçon inexistante : {scope}")
                conn.commit()
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur add_contradiction : {e}")

    def get_active_lessons(self, entity_types: List[str], environment: str, limit: int = 300) -> List[Dict[str, Any]]:
        """
        Retourne TOUTES les leçons actives pour les entity_types et l'environnement donnés,
        SANS filtre de confiance/evidence ni correspondance lexicale en amont.
        """
        if not entity_types:
            return []
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
                    except Exception:
                        d["keywords"] = []
                    # polarity already present
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_active_lessons : {e}")
            return []

    def get_lessons(self, entity_type: str, scope_like: str, min_confidence: float = 0.6,
                    min_evidence: int = 3, environment: str = "simulated") -> List[Dict[str, Any]]:
        """
        DÉPRÉCIÉ : conservé pour compatibilité mais plus appelé par Advisor.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM lessons
                    WHERE entity_type = ?
                      AND scope LIKE ?
                      AND is_active = 1
                      AND confidence >= ?
                      AND evidence_count >= ?
                      AND environment = ?
                    ORDER BY confidence DESC, evidence_count DESC
                ''', (entity_type, f"%{scope_like}%", min_confidence, min_evidence, environment))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_lessons : {e}")
            return []

    def get_all_episodes_unanalyzed(self) -> List[Dict[str, Any]]:
        """
        Récupère tous les épisodes avec un arbre non vide pour analyse.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM episodes
                    WHERE execution_tree_json IS NOT NULL AND execution_tree_json != '{}'
                ''')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_all_episodes_unanalyzed : {e}")
            return []

    def get_unconsolidated_groups(self) -> List[Dict[str, Any]]:
        """
        Retourne les groupes (entity_type, scope, environment) qui ont au moins
        ENTITY_LEARNER_MIN_EVIDENCE leçons brutes non consolidées.
        """
        from core.constants import ENTITY_LEARNER_MIN_EVIDENCE
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # Requête simplifiée (on suppose que is_consolidated existe)
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
                rows = cursor.fetchall()
                # Log pour debug
                Logger.info(f"[LessonStore] get_unconsolidated_groups: {len(rows)} groupe(s) trouvé(s).")
                return [dict(row) for row in rows]
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_unconsolidated_groups : {e}")
            return []
        
        
    def get_brute_lessons_by_group(self, entity_type: str, scope: str, environment: str) -> List[Dict[str, Any]]:
        """Récupère toutes les leçons brutes d’un groupe."""
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
                    except Exception:
                        d["keywords"] = []
                    try:
                        d["source_episodes"] = json.loads(d.pop("source_episodes_json") or "[]")
                    except Exception:
                        d["source_episodes"] = []
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_brute_lessons_by_group : {e}")
            return []

    def get_consolidated_lessons(self, entity_types: List[str], environment: str) -> List[Dict[str, Any]]:
        """Récupère les leçons consolidées pour les entités et environnement donnés."""
        if not entity_types:
            return []
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
                    except Exception:
                        d["keywords"] = []
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_consolidated_lessons : {e}")
            return []

    def create_consolidated_lesson(self, entity_type: str, scope: str, environment: str,
                                    from_lesson_id: int, recommendation: str,
                                    confidence: float, evidence_count: int,
                                    contradiction_count: int, keywords: List[str],
                                    polarity: str, source_episodes: List[str],
                                    conflict_resolution: Optional[str] = None) -> int:
        """
        Insère une leçon consolidée à partir d’une brute gagnante (ou d’une synthèse).
        Retourne l’ID de la nouvelle leçon.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO lessons (
                        entity_type, scope, environment, is_consolidated,
                        consolidated_from_id, recommendation, confidence,
                        evidence_count, contradiction_count, keywords_json,
                        source_episodes_json, polarity, conflict_resolution
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    entity_type, scope, environment,
                    from_lesson_id, recommendation, confidence,
                    evidence_count, contradiction_count,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(source_episodes, ensure_ascii=False),
                    polarity, conflict_resolution
                ))
                conn.commit()
                new_id = cursor.lastrowid
                Logger.info(f"[LessonStore] Leçon consolidée créée : id={new_id}, scope={scope}, conf={confidence:.2f}")
                return new_id
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur create_consolidated_lesson : {e}")
            return 0
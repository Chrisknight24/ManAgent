# memory/lesson_store.py
# =====================================================
# PHASE 3 – STOCKAGE DES LEÇONS (KnowledgeBase)
# Gère la table 'lessons' dans la base SQLite.
# =====================================================
# memory/lesson_store.py
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
                        last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_scope ON lessons(scope)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_lessons_entity ON lessons(entity_type)')

                # --- MIGRATION : ajout de keywords_json si la table préexistait sans cette colonne ---
                try:
                    cursor.execute("ALTER TABLE lessons ADD COLUMN keywords_json TEXT DEFAULT '[]'")
                    Logger.info("[LessonStore] Migration: colonne 'keywords_json' ajoutée.")
                except sqlite3.OperationalError:
                    pass

                # --- MIGRATION : source_episodes_json — quels mission_id ont contribué à cette
                # leçon (créée puis confirmée). Nécessaire pour la traçabilité demandée dans la
                # couche d'observabilité ("de quelle mission vient cette leçon ?").
                try:
                    cursor.execute("ALTER TABLE lessons ADD COLUMN source_episodes_json TEXT DEFAULT '[]'")
                    Logger.info("[LessonStore] Migration: colonne 'source_episodes_json' ajoutée.")
                except sqlite3.OperationalError:
                    pass

                conn.commit()
                Logger.info("[LessonStore] Table 'lessons' prête.")
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur d'initialisation : {e}")

    # Plafonds défensifs — évitent la dérive observée en test réel (une leçon avec 55
    # mots-clés accumulés au fil des confirmations, qui gonfle le prompt du reranker pour rien).
    MAX_KEYWORDS_PER_CALL = 6
    MAX_KEYWORDS_TOTAL = 20
    MAX_SOURCE_EPISODES = 50

    # =====================================================
    # UPSERT (Clé = entity_type + scope + environment)
    # =====================================================

    def upsert_lesson(self, entity_type: str, scope: str, recommendation: str,
                       environment: str = "simulated", keywords: Optional[List[str]] = None,
                       mission_id: Optional[str] = None) -> None:
        """
        Ajoute ou met à jour une leçon.
        Clé d'unicité : (entity_type, scope, environment). `scope` doit rester une identité
        STABLE et étroite (c'est la clé d'evidence) — `keywords` est volontairement plus large
        et permissif : c'est la couche de découvrabilité que consulte le reranker LLM, pas
        l'identité de la leçon. Les nouveaux mots-clés s'AJOUTENT à chaque confirmation
        (union, jamais d'écrasement) : chaque épisode peut révéler un angle différent de la
        même situation — mais bornés (voir MAX_KEYWORDS_*), sinon la liste grossit sans fin
        (observé en test réel : une leçon avec 55 mots-clés après quelques confirmations).

        `mission_id`, s'il est fourni, alimente `source_episodes_json` — la liste des missions
        qui ont contribué à cette leçon, pour permettre de remonter de la leçon vers la mission
        d'origine dans la couche d'observabilité.
        """
        # Plafond appliqué dès la réception, indépendamment de ce que le LLM a proposé
        keywords = (keywords or [])[:self.MAX_KEYWORDS_PER_CALL]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, evidence_count, contradiction_count, keywords_json, source_episodes_json "
                    "FROM lessons WHERE entity_type = ? AND scope = ? AND environment = ?",
                    (entity_type, scope, environment)
                )
                row = cursor.fetchone()
                if row:
                    lesson_id, evidence_count, contradiction_count, existing_keywords_json, existing_sources_json = row
                    new_evidence = evidence_count + 1
                    # Laplace : (evidence+1)/(evidence+contradiction+2)
                    new_confidence = (new_evidence + 1) / (new_evidence + contradiction_count + 2)
                    try:
                        existing_keywords = json.loads(existing_keywords_json) if existing_keywords_json else []
                    except Exception:
                        existing_keywords = []
                    merged_keywords = sorted(set(existing_keywords) | set(keywords))[:self.MAX_KEYWORDS_TOTAL]

                    try:
                        existing_sources = json.loads(existing_sources_json) if existing_sources_json else []
                    except Exception:
                        existing_sources = []
                    if mission_id and mission_id not in existing_sources:
                        existing_sources.append(mission_id)
                    merged_sources = existing_sources[-self.MAX_SOURCE_EPISODES:]

                    cursor.execute('''
                        UPDATE lessons
                        SET evidence_count = ?,
                            confidence = ?,
                            recommendation = ?,
                            keywords_json = ?,
                            source_episodes_json = ?,
                            last_verified_at = ?
                        WHERE id = ?
                    ''', (new_evidence, new_confidence, recommendation,
                          json.dumps(merged_keywords, ensure_ascii=False),
                          json.dumps(merged_sources, ensure_ascii=False),
                          datetime.now().isoformat(), lesson_id))
                    Logger.debug(f"[LessonStore] Mise à jour leçon : {scope} (evidence={new_evidence}, conf={new_confidence:.2f}, keywords={len(merged_keywords)})")
                else:
                    # Nouvelle leçon : confiance initiale 2/3 (Laplace avec ev=1, cont=0)
                    initial_confidence = 2 / 3
                    sources = [mission_id] if mission_id else []
                    cursor.execute('''
                        INSERT INTO lessons (entity_type, scope, recommendation, environment, confidence, evidence_count, keywords_json, source_episodes_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (entity_type, scope, recommendation, environment, initial_confidence, 1,
                          json.dumps(sorted(set(keywords)), ensure_ascii=False),
                          json.dumps(sources, ensure_ascii=False)))
                    Logger.debug(f"[LessonStore] Nouvelle leçon : {scope} (keywords={keywords})")
                conn.commit()
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

        C'est le remplacement volontaire de l'ancien get_lessons() (LIKE + seuils durs) :
        le jugement de pertinence et de fiabilité est maintenant délégué à un LLM reranker
        qui voit confidence/evidence_count/contradiction_count comme du CONTEXTE pour sa
        décision, pas comme une porte binaire qui filtre avant même qu'il ne les voie —
        c'était la cause du cercle vicieux (une leçon à evidence=1 n'était jamais montrée,
        donc jamais confirmée, donc jamais promue).

        Le seul filtre qui reste dur, non négociable, et JAMAIS délégué au LLM : environment.
        Une leçon 'simulated' ne doit jamais apparaître dans une requête 'real', point final.

        `limit` est un plafond défensif (garde le prompt du reranker borné) ; à revisiter avec
        des embeddings le jour où ce plafond devient un vrai facteur limitant, pas avant.
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
                           evidence_count, contradiction_count, keywords_json
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
                    results.append(d)
                return results
        except Exception as e:
            Logger.error(f"[LessonStore] Erreur get_active_lessons : {e}")
            return []

    def get_lessons(self, entity_type: str, scope_like: str, min_confidence: float = 0.6,
                    min_evidence: int = 3, environment: str = "simulated") -> List[Dict[str, Any]]:
        """
        DÉPRÉCIÉ : conservé pour compatibilité mais plus appelé par Advisor (voir get_active_lessons).
        Le filtre LIKE + seuils durs en amont est précisément ce qui cassait la découvrabilité
        (vocabulaire scope != vocabulaire du but de mission) et créait un cercle vicieux sur
        l'evidence. Ne pas rebrancher ce chemin sans revoir cette décision consciemment.
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
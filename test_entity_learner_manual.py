# test_entity_learner_manual.py
import asyncio
import sqlite3
import sys
import os
import tempfile
import shutil
import json
sys.path.insert(0, os.getcwd())

from memory.lesson_store import LessonStore
from core.entity_learner import EntityLearner
from core.constants import ENTITY_LEARNER_MIN_EVIDENCE

async def test_consolidation():
    print("🧪 Test EntityLearner - Consolidation")
    
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    print(f"📁 Base temporaire : {db_path}")
    
    # 1. Base sur fichier
    store = LessonStore(db_path)
    print("✅ LessonStore créée.")
    
    # 2. Insérer 3 leçons brutes distinctes via SQL (pour éviter l'upsert)
    with store._get_connection() as conn:
        cursor = conn.cursor()
        for i in range(3):
            cursor.execute('''
                INSERT INTO lessons (
                    entity_type, scope, recommendation, environment,
                    confidence, evidence_count, keywords_json,
                    source_episodes_json, polarity, is_consolidated, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                "Planner",
                "test_open_notepad",
                f"Éviter de cliquer sur le bouton X (test {i})",
                "simulated",
                2/3,
                1,
                json.dumps(["notepad", "close"]),
                json.dumps([f"mission_{i}"]),
                "avoid",
                0,
                1
            ))
        conn.commit()
    print("✅ 3 leçons avoid insérées (via SQL direct).")

    # Diagnostic : afficher le contenu
    with store._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, scope, is_consolidated, is_active FROM lessons")
        rows = cursor.fetchall()
        print("🔍 Contenu de la table lessons :")
        for row in rows:
            print(dict(row))
    
    # 3. Vérifier que le groupe est candidat
    groups = store.get_unconsolidated_groups()
    print(f"📊 Groupes candidats : {groups}")
    assert len(groups) == 1, f"Un groupe devrait être candidat, trouvé {len(groups)}"
    assert groups[0]["scope"] == "test_open_notepad"
    
    # 4. Exécuter la consolidation
    learner = EntityLearner(lesson_store=store, cache_manager=None)
    count = await learner.consolidate_if_needed()
    print(f"✅ Consolidation exécutée : {count} groupe(s) traités.")
    
    # 5. Vérifier la leçon consolidée
    consolidated = store.get_consolidated_lessons(["Planner"], "simulated")
    print(f"📊 Leçons consolidées : {consolidated}")
    assert len(consolidated) == 1, "Une leçon consolidée devrait exister"
    assert consolidated[0]["scope"] == "test_open_notepad"
    
    # 6. Vérifier les brutes (toujours présentes)
    brutes = store.get_brute_lessons_by_group("Planner", "test_open_notepad", "simulated")
    print(f"📊 Leçons brutes encore présentes : {len(brutes)}")
    assert len(brutes) == 3, "Les 3 brutes doivent rester actives"
    
    print("🎉 Test réussi !")
    
    shutil.rmtree(temp_dir)
    return True

if __name__ == "__main__":
    asyncio.run(test_consolidation())
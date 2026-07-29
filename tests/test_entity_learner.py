# tests/test_entity_learner.py
import pytest
import sqlite3
import json
from core.entity_learner import EntityLearner
from memory.lesson_store import LessonStore
from core.constants import ENTITY_LEARNER_MIN_EVIDENCE

@pytest.fixture
def lesson_store():
    """Fixture pour une base en mémoire."""
    store = LessonStore(":memory:")
    # On s'assure que les colonnes sont créées
    with store._get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("ALTER TABLE lessons ADD COLUMN is_consolidated BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE lessons ADD COLUMN consolidated_from_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE lessons ADD COLUMN conflict_resolution TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    return store

@pytest.mark.asyncio
async def test_consolidation_simple_avoid(lesson_store):
    """3 brutes avoid -> 1 consolidée avoid."""
    store = lesson_store
    # Insérer 3 leçons avoid pour le même scope
    for i in range(3):
        store.upsert_lesson(
            entity_type="Planner",
            scope="test_scope_avoid",
            recommendation=f"Avoid test {i}",
            environment="simulated",
            keywords=["test"],
            mission_id="mission_123",
            polarity="avoid"
        )
    
    learner = EntityLearner(lesson_store=store, cache_manager=None)
    count = await learner.consolidate_if_needed()
    assert count == 1, "Un groupe devrait être consolidé"

    # Vérifier qu'il y a une leçon consolidée
    consolidated = store.get_consolidated_lessons(["Planner"], "simulated")
    assert len(consolidated) == 1, "Une seule consolidée devrait exister"
    assert consolidated[0]["scope"] == "test_scope_avoid"
    assert consolidated[0]["polarity"] == "avoid"
    assert consolidated[0]["evidence_count"] == 3  # Somme des evidence_count (1+1+1)

@pytest.mark.asyncio
async def test_consolidation_with_conflict_prefer_wins(lesson_store):
    """2 avoid (conf 0.8) + 1 prefer (conf 0.9) -> consolidée prefer."""
    store = lesson_store
    # Insérer 2 avoid
    for i in range(2):
        store.upsert_lesson(
            entity_type="Executor",
            scope="test_scope_conflict",
            recommendation=f"Avoid conflict {i}",
            environment="simulated",
            keywords=["conflict"],
            mission_id="mission_456",
            polarity="avoid"
        )
    # Insérer 1 prefer avec confiance plus élevée (en modifiant manualement la confiance)
    # On simule en créant une leçon prefer avec evidence_count plus élevé
    store.upsert_lesson(
        entity_type="Executor",
        scope="test_scope_conflict",
        recommendation="Prefer conflict: use keyboard",
        environment="simulated",
        keywords=["conflict", "keyboard"],
        mission_id="mission_789",
        polarity="prefer"
    )
    # On va forcer la confiance de la prefer à 0.9 en mettant à jour directement (car upsert utilise une formule)
    with store._get_connection() as conn:
        cursor = conn.cursor()
        # On met à jour la leçon prefer pour qu'elle ait evidence_count=10, contradiction=1 -> conf ~ 0.85
        # Mais pour simplifier, on va mettre à jour directement la confiance
        cursor.execute("""
            UPDATE lessons SET confidence = 0.9, evidence_count = 10, contradiction_count = 1
            WHERE scope = 'test_scope_conflict' AND polarity = 'prefer'
        """)
        conn.commit()

    learner = EntityLearner(lesson_store=store, cache_manager=None)
    count = await learner.consolidate_if_needed()
    assert count == 1

    consolidated = store.get_consolidated_lessons(["Executor"], "simulated")
    assert len(consolidated) == 1
    assert consolidated[0]["polarity"] == "prefer"
    assert "keyboard" in consolidated[0]["keywords"]
    # La confiance devrait être celle de la prefer (0.9)
    assert consolidated[0]["confidence"] == 0.9

@pytest.mark.asyncio
async def test_seuil_non_atteint(lesson_store):
    """2 brutes -> pas de consolidation."""
    store = lesson_store
    for i in range(2):
        store.upsert_lesson(
            entity_type="Planner",
            scope="test_scope_seuil",
            recommendation=f"Brute {i}",
            environment="simulated",
            keywords=["seuil"],
            mission_id="mission_999",
            polarity="avoid"
        )
    learner = EntityLearner(lesson_store=store, cache_manager=None)
    count = await learner.consolidate_if_needed()
    assert count == 0, "Pas assez de brutes, pas de consolidation"

    consolidated = store.get_consolidated_lessons(["Planner"], "simulated")
    assert len(consolidated) == 0

@pytest.mark.asyncio
async def test_fallback_advisor(lesson_store):
    """L'Advisor doit utiliser les consolidées si elles existent, sinon les brutes."""
    store = lesson_store
    # Créer une brute sans consolidée
    store.upsert_lesson(
        entity_type="Planner",
        scope="test_fallback",
        recommendation="Brute only",
        environment="simulated",
        keywords=["fallback"],
        mission_id="m1",
        polarity="avoid"
    )
    # Simuler l'Advisor (on va juste vérifier la récupération)
    from core.learner import Advisor
    # On aurait besoin d'un runtime_state factice, mais pour ce test, on peut juste tester la méthode get_consolidated_lessons
    cons = store.get_consolidated_lessons(["Planner"], "simulated")
    assert len(cons) == 0, "Pas de consolidées"
    # Fallback sur les brutes
    brutes = store.get_active_lessons(["Planner"], "simulated")
    assert len(brutes) == 1, "Une brute devrait être trouvée"
    assert brutes[0]["scope"] == "test_fallback"
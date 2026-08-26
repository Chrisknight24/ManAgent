"""
core/discovery/providers/facts_provider.py
======================================================
DataProvider pour l'accès aux faits sémantiques et préférences appris (LessonStore).
Permet à l'Orchestrateur d'explorer à la demande l'ensemble des connaissances
mémorisées (`semantic_fact`, préférences utilisateur, profils).
"""

from typing import List, Dict, Any, Optional
import json
from pydantic import Field
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset
from utils.logger import Logger


class FactsDataAsset(DataAsset):
    """Asset représentant une collection de faits mémorisés."""
    data: Dict[str, Any] = Field(default_factory=dict)

    def dump_data(self) -> str:
        try:
            return json.dumps(self.data, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"[Erreur de sérialisation des faits: {e}]"


class FactsProvider(DataProvider):
    """Fournit les faits mémorisés (scope='semantic_fact' ou entités globales)."""

    def __init__(self, lesson_store):
        self.lesson_store = lesson_store
        self._targets = ["user_profile", "preferences", "all_facts"]

    def get_data_type(self) -> str:
        return "facts"

    def get_targets(self) -> List[str]:
        return self._targets

    def get_asset(self, target: str) -> DataAsset:
        try:
            with self.lesson_store._get_connection() as conn:
                cursor = conn.cursor()
                
                if target == "preferences":
                    cursor.execute("""
                        SELECT id, entity_type, scope, recommendation, created_at
                        FROM lessons
                        WHERE is_active = 1
                          AND (scope = 'semantic_fact' OR entity_type IN ('Global', 'Orchestrator'))
                          AND (recommendation LIKE '%préfère%' OR recommendation LIKE '%souhaite%' OR recommendation LIKE '%veut%' OR recommendation LIKE '%style%')
                        ORDER BY id DESC
                    """)
                elif target == "user_profile":
                    cursor.execute("""
                        SELECT id, entity_type, scope, recommendation, created_at
                        FROM lessons
                        WHERE is_active = 1
                          AND (scope = 'semantic_fact' OR entity_type IN ('Global', 'Orchestrator'))
                          AND (recommendation LIKE '%nom%' OR recommendation LIKE '%prénom%' OR recommendation LIKE '%âge%' OR recommendation LIKE '%travail%' OR recommendation LIKE '%langue%' OR recommendation LIKE '%utilisateur%')
                        ORDER BY id DESC
                    """)
                else:  # all_facts ou fallback
                    cursor.execute("""
                        SELECT id, entity_type, scope, recommendation, created_at
                        FROM lessons
                        WHERE is_active = 1
                          AND (scope = 'semantic_fact' OR entity_type IN ('Global', 'Orchestrator'))
                        ORDER BY id DESC
                    """)
                
                rows = cursor.fetchall()
                facts_data = [
                    {
                        "id": r[0],
                        "entity_type": r[1],
                        "scope": r[2],
                        "fact": r[3],
                        "created_at": str(r[4])
                    }
                    for r in rows
                ]
                
                metadata = {
                    "target": target,
                    "count": len(facts_data),
                    "description": f"Faits mémorisés pour la cible '{target}'"
                }
                
                return FactsDataAsset(
                    target_id=target,
                    metadata=metadata,
                    data={"target": target, "facts": facts_data, "count": len(facts_data)}
                )
        except Exception as e:
            Logger.error(f"[FactsProvider] Erreur lors de la récupération de la cible '{target}': {e}")
            return FactsDataAsset(
                target_id=target,
                metadata={"target": target, "error": str(e), "count": 0},
                data={"target": target, "facts": [], "error": str(e)}
            )

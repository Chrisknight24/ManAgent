"""
tools/test_retrieval_lab.py
============================
Laboratoire d'expérimentation pour calibrer les embeddings.
Version asynchrone.
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.embedding_service import get_embedding_service
from memory.mission_profile_store import MissionProfileStore

CORPUS = [
    ("ouvrir chrome", "ouvrir", "chrome"),
    ("lancer chrome", "lancer", "chrome"),
    ("démarrer chrome", "démarrer", "chrome"),
    ("open chrome", "open", "chrome"),
    ("ouvrir Google Chrome", "ouvrir", "google chrome"),
    ("lancer Google Chrome", "lancer", "google chrome"),
    ("fermer chrome", "fermer", "chrome"),
    ("quitter chrome", "quitter", "chrome"),
    ("ouvrir edge", "ouvrir", "edge"),
    ("lancer edge", "lancer", "edge"),
    ("ouvrir firefox", "ouvrir", "firefox"),
    ("ouvrir notepad", "ouvrir", "notepad"),
    ("fermer excel", "fermer", "excel"),
    ("quitter excel", "quitter", "excel"),
    ("supprimer fichier", "supprimer", "fichier"),
    ("renommer dossier", "renommer", "dossier"),
]

QUERIES = [
    "lancer chrome",
    "ouvrir edge",
    "fermer excel",
    "supprimer fichier",
]

async def main():
    print("🧪 LABORATOIRE D'EMBEDDING — CALIBRATION DES SEUILS")
    print("=" * 70)

    embedder = get_embedding_service()
    store = MissionProfileStore()

    print("\n📥 Insertion du corpus de test...")
    for i, (sig, action, obj) in enumerate(CORPUS):
        embedding = await embedder.embed(sig)
        print(len(embedding))
        store.insert_profile(
            mission_id=f"test-{i+1:03d}",
            signature_text=sig,
            embedding=embedding,
            action=action,
            object=obj,
            signature_index=0,
            signature_count=1
        )
    print(f"✅ {len(CORPUS)} signatures insérées.\n")

    with store._get_connection() as conn:
        store._ensure_extension_loaded(conn)
        count = conn.execute("SELECT COUNT(*) FROM vec_mission_profiles").fetchone()[0]
        print(f"✅ {count} lignes dans l'index vectoriel.")

    for query in QUERIES:
        print(f"🔍 Query : '{query}'")
        q_embedding = await embedder.embed(query)
        results = store.get_similar_profiles(q_embedding, top_k=20, threshold=2.0)

        print(f"   Top {len(results)} résultats :")
        for i, r in enumerate(results, 1):
            sim = r['similarity']
            sig = r['signature_text']
            mid = r['mission_id']
            print(f"   {i:2d}) {sig} (mission: {mid}) -> {sim:.4f}")

        if results:
            scores = [r['similarity'] for r in results]
            print(f"   📊 Min: {min(scores):.4f} | Max: {max(scores):.4f} | Moy: {sum(scores)/len(scores):.4f}")
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(main())
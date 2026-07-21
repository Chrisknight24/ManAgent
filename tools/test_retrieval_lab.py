"""
tools/test_retrieval_lab.py
============================
Laboratoire d'expérimentation pour calibrer les embeddings.
Version améliorée : compare plusieurs modèles installés.
"""

import sys
import os
import asyncio
import json
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.runtime_state import RuntimeState
from embeddings import EmbeddingProviderManager
from embeddings.providers.sentence_transformer import SentenceTransformerProvider
from memory.mission_profile_store import MissionProfileStore

# Corpus de test (français, anglais, mixte)
CORPUS = [
    ("ouvrir chrome", "ouvrir", "chrome"),
    ("lancer chrome", "lancer", "chrome"),
    ("démarrer chrome", "démarrer", "chrome"),
    ("open chrome", "open", "chrome"),
    ("ouvrir Google Chrome", "ouvrir", "google chrome"),
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
    ("ouvrir le gestionnaire de tâches", "ouvrir", "gestionnaire de tâches"),
]

QUERIES = [
    "lancer chrome",
    "ouvrir edge",
    "fermer excel",
    "supprimer fichier",
    "ouvrir le gestionnaire de tâches",
]

# Modèles à tester
MODELS_TO_TEST = [
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "display": "MiniLM L6 (anglais)",
        "prefix_query": "",
        "prefix_passage": ""
    },
    {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "display": "Multilingual MiniLM L12",
        "prefix_query": "",
        "prefix_passage": ""
    },
    {
        "id": "intfloat/multilingual-e5-small",
        "display": "E5 Small (multilingual)",
        "prefix_query": "query: ",
        "prefix_passage": "passage: "
    }
]

async def run_benchmark():
    print("🧪 BENCHMARK DES MODÈLES D'EMBEDDING")
    print("=" * 70)

    runtime_state = RuntimeState()
    manager = EmbeddingProviderManager()
    runtime_state.embedding_manager = manager

    # Enregistrer tous les modèles
    for m in MODELS_TO_TEST:
        provider = SentenceTransformerProvider(
            model_id=m["id"],
            display_name=m["display"],
            prefix_query=m["prefix_query"],
            prefix_passage=m["prefix_passage"]
        )
        manager.register_provider(provider)

    store = MissionProfileStore()

    # Nettoyer les anciennes données de test
    print("\n🧹 Nettoyage des anciennes données de test...")
    # (on ne supprime pas pour l'instant, on utilise des mission_id uniques)

    # Pour chaque modèle, on insère le corpus
    for model in MODELS_TO_TEST:
        provider = manager.get_provider(model["id"])
        await provider.initialize()
        manager.set_active_provider(model["id"])

        print(f"\n📥 Insertion du corpus avec {model['display']}...")
        for i, (sig, action, obj) in enumerate(CORPUS):
            embedding = await manager.embed(sig)
            store.insert_profile(
                mission_id=f"test-{model['id'].replace('/', '_')[:20]}-{i+1:03d}",
                signature_text=sig,
                embedding=embedding,
                action=action,
                object=obj,
                signature_index=0,
                signature_count=1,
                embedding_model=model["id"]
            )
        print(f"✅ {len(CORPUS)} signatures insérées.")

    # Pour chaque modèle, on interroge
    print("\n" + "=" * 70)
    print("🔍 RÉSULTATS DES REQUÊTES")
    print("=" * 70)

    for model in MODELS_TO_TEST:
        provider = manager.get_provider(model["id"])
        await provider.initialize()
        manager.set_active_provider(model["id"])

        print(f"\n📊 MODÈLE : {model['display']}")
        print("-" * 50)

        for query in QUERIES:
            print(f"\n🔎 Requête : '{query}'")
            q_embedding = await manager.embed(query)
            results = store.get_similar_profiles(
                q_embedding,
                top_k=10,
                threshold=0.0,
                embedding_model=model["id"]  # filtrage par modèle
            )

            if results:
                # On affiche les 5 premiers
                for i, r in enumerate(results[:5], 1):
                    sim = r['similarity']
                    sig = r['signature_text']
                    print(f"   {i:2d}) {sig} -> {sim:.4f}")

                scores = [r['similarity'] for r in results]
                print(f"   📊 Min: {min(scores):.4f} | Max: {max(scores):.4f} | Moy: {sum(scores)/len(scores):.4f}")
            else:
                print("   Aucun résultat.")

    print("\n✅ Benchmark terminé.")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
"""
tools/embedding_benchmark.py
=============================
Benchmark permanent pour évaluer les modèles d'embedding.

Calcule la similarité cosinus sur un corpus de test varié,
sans utiliser SQLite, pour isoler la qualité du modèle.

Usage:
    python tools/embedding_benchmark.py
    python tools/embedding_benchmark.py --model sentence-transformers/all-MiniLM-L6-v2
    python tools/embedding_benchmark.py --compare  # compare plusieurs modèles
"""

import sys
import os
import asyncio
import argparse
from typing import List, Tuple, Dict
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.embedding_service import get_embedding_service, EmbeddingService

# ============================================================
# 1. CORPUS DE TEST (paires avec similarité attendue – pour analyse)
# ============================================================

# Chaque paire est (phrase1, phrase2, label_attendu)
# Le label est une estimation qualitative : "proche", "moyen", "lointain"
# Il n'est pas utilisé pour le calcul, seulement pour l'analyse.
TEST_PAIRS = [
    # Synonymes stricts
    ("ouvrir chrome", "lancer chrome", "proche"),
    ("ouvrir chrome", "démarrer chrome", "proche"),
    ("lancer chrome", "démarrer chrome", "proche"),
    ("fermer chrome", "quitter chrome", "proche"),
    ("ouvrir edge", "lancer edge", "proche"),
    ("fermer excel", "quitter excel", "proche"),

    # Antonymes
    ("ouvrir chrome", "fermer chrome", "opposé"),
    ("ouvrir edge", "fermer edge", "opposé"),
    ("lancer chrome", "quitter chrome", "opposé"),

    # Objets proches
    ("ouvrir chrome", "ouvrir edge", "proche_objet"),
    ("ouvrir chrome", "ouvrir firefox", "proche_objet"),
    ("lancer chrome", "lancer edge", "proche_objet"),
    ("fermer chrome", "fermer edge", "proche_objet"),

    # Objets différents
    ("ouvrir chrome", "fermer excel", "différent"),
    ("ouvrir chrome", "ouvrir notepad", "différent"),
    ("lancer chrome", "fermer excel", "différent"),
    ("fermer chrome", "ouvrir notepad", "différent"),
    ("supprimer fichier", "renommer dossier", "différent"),

    # Variantes de formulation
    ("ouvrir chrome", "open chrome", "proche"),
    ("lancer chrome", "launch chrome", "proche"),
    ("fermer chrome", "close chrome", "proche"),
]

# Requêtes supplémentaires pour tester la discrimination
QUERY_SET = [
    "ouvrir chrome",
    "lancer chrome",
    "fermer chrome",
    "ouvrir edge",
    "fermer excel",
    "supprimer fichier",
]

# Tous les textes uniques du corpus
ALL_TEXTS = sorted(set([p[0] for p in TEST_PAIRS] + [p[1] for p in TEST_PAIRS] + QUERY_SET))


# ============================================================
# 2. FONCTIONS DE BENCHMARK (asynchrones)
# ============================================================

async def compute_similarity_matrix(texts: List[str], embedder: EmbeddingService) -> np.ndarray:
    """Calcule la matrice de similarité cosinus pour une liste de textes."""
    embeddings = []
    for t in texts:
        emb = await embedder.embed(t)
        embeddings.append(emb)
    return cosine_similarity(embeddings)

async def run_benchmark(model_name: str) -> Dict:
    """Exécute le benchmark pour un modèle donné et retourne les métriques."""
    print(f"\n🔍 Benchmark du modèle : {model_name}")
    embedder = EmbeddingService(model_name=model_name)

    # 1. Matrice de similarité pour toutes les paires
    all_texts = ALL_TEXTS
    matrix = await compute_similarity_matrix(all_texts, embedder)

    # 2. Scores pour les paires de test
    pair_scores = []
    for p1, p2, label in TEST_PAIRS:
        idx1 = all_texts.index(p1)
        idx2 = all_texts.index(p2)
        score = matrix[idx1, idx2]
        pair_scores.append((p1, p2, label, score))

    # 3. Pour chaque requête, top 5 des textes les plus proches
    query_results = {}
    for query in QUERY_SET:
        q_idx = all_texts.index(query)
        scores = matrix[q_idx]
        # On exclut la requête elle-même (score = 1.0)
        sorted_indices = np.argsort(scores)[::-1]
        top = []
        for idx in sorted_indices:
            if all_texts[idx] != query:
                top.append((all_texts[idx], scores[idx]))
                if len(top) >= 5:
                    break
        query_results[query] = top

    # 4. Statistiques globales
    all_scores = [s for _, _, _, s in pair_scores]
    stats = {
        "model": model_name,
        "mean": np.mean(all_scores),
        "std": np.std(all_scores),
        "min": np.min(all_scores),
        "max": np.max(all_scores),
        "pair_scores": pair_scores,
        "query_results": query_results,
    }
    return stats

def print_report(stats: Dict):
    """Affiche un rapport lisible dans la console."""
    print("\n" + "=" * 70)
    print(f"📊 RAPPORT DE BENCHMARK – {stats['model']}")
    print("=" * 70)

    print(f"\nStatistiques globales (similarité cosinus) :")
    print(f"  Moyenne : {stats['mean']:.4f}")
    print(f"  Écart-type : {stats['std']:.4f}")
    print(f"  Min : {stats['min']:.4f}")
    print(f"  Max : {stats['max']:.4f}")

    print("\n🔍 Paires de test (échantillon) :")
    for p1, p2, label, score in stats['pair_scores'][:10]:
        print(f"  {p1} <-> {p2} : {score:.4f} ({label})")

    print("\n🔍 Top 5 pour chaque requête :")
    for query, top in stats['query_results'].items():
        print(f"  '{query}' :")
        for text, score in top:
            print(f"    - {text} ({score:.4f})")

async def compare_models(models: List[str]):
    """Compare plusieurs modèles et affiche un tableau comparatif."""
    results = []
    for model in models:
        stats = await run_benchmark(model)
        results.append(stats)

    print("\n" + "=" * 70)
    print("📊 COMPARAISON DES MODÈLES")
    print("=" * 70)
    print(f"{'Modèle':<50} | {'Moyenne':<8} | {'Std':<8} | {'Min':<8} | {'Max':<8}")
    print("-" * 90)
    for r in results:
        print(f"{r['model']:<50} | {r['mean']:.4f} | {r['std']:.4f} | {r['min']:.4f} | {r['max']:.4f}")

# ============================================================
# 3. POINT D'ENTRÉE
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="Benchmark des modèles d'embedding")
    parser.add_argument("--model", type=str, default=None,
                        help="Nom du modèle à tester (ex: sentence-transformers/all-MiniLM-L6-v2)")
    parser.add_argument("--compare", action="store_true",
                        help="Compare plusieurs modèles prédéfinis")
    args = parser.parse_args()

    if args.compare:
        models = [
            "sentence-transformers/all-MiniLM-L6-v2",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "intfloat/multilingual-e5-small",
        ]
        await compare_models(models)
    else:
        model = args.model or "sentence-transformers/all-MiniLM-L6-v2"
        stats = await run_benchmark(model)
        print_report(stats)

if __name__ == "__main__":
    asyncio.run(main())
"""
core/retriever.py
=================
Service de retrieval vectoriel des missions simples (MVP).

Rôle :
- Recevoir une liste de MissionSignature (action + objet + desired_state)
- Calculer l'embedding pour chaque signature
- Interroger la base vectorielle (sqlite-vec) via MissionProfileStore
- Filtrer par seuil de similarité
- Retourner les missions similaires avec leur résumé (depuis episodes.presentator_result_json)
"""

import asyncio
from typing import List, Dict, Any, Optional, Set
from core.embedding_service import get_embedding_service
from memory.mission_profile_store import MissionProfileStore
from memory.mission_store import MissionStore
from utils.logger import Logger
from core.plan_models import MissionSignature
from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD


class Retriever:
    """
    Retrieval vectoriel de missions simples.
    """

    def __init__(
        self,
        top_k: int = RETRIEVAL_TOP_K,
        threshold: float = RETRIEVAL_THRESHOLD
    ):
        self.top_k = top_k
        self.threshold = threshold
        self.profile_store = MissionProfileStore()
        self.mission_store = MissionStore()
        self.embedding_service = get_embedding_service()

    async def retrieve(
        self,
        signatures: List[MissionSignature],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        query_mission_id: Optional[str] = None,   # <--- NOUVEAU
    ) -> List[Dict[str, Any]]:
        """
        Point d'entrée principal.

        Pour chaque signature, calcule l'embedding, interroge la base,
        filtre par seuil, et retourne les missions uniques avec leur résumé.

        :param signatures: Liste des MissionSignature extraites par l'Orchestrateur.
        :param top_k: Nombre de voisins à récupérer (par défaut self.top_k).
        :param threshold: Seuil de similarité (par défaut self.threshold).
        :return: Liste de dictionnaires contenant :
                 - mission_id
                 - goal (objectif de la mission)
                 - summary (résumé du Presentator, ou goal en fallback)
                 - score (similarité maximale parmi les signatures)
                 - matched_signature (la signature qui a matché)
        """
        if not signatures:
            Logger.debug("[Retriever] Aucune signature fournie, retrieval ignoré.")
            return []
        
        Logger.event(
            "retriever_query",
            signatures=[{"action": s.action, "object": s.object, "desired_state": s.desired_state} for s in signatures],
            top_k=self.top_k,
            threshold=self.threshold
        )

        top_k = top_k or self.top_k
        threshold = threshold or self.threshold

        # Étape 1 : Calcul des embeddings et requêtes vectorielles
        all_candidates: Dict[str, Dict] = {}  # mission_id -> {score, matched_signature}

        for sig in signatures:
            # Construire le texte de la signature
            signature_text = f"{sig.action} {sig.object}"
            if sig.desired_state:
                signature_text += f" {sig.desired_state}"

            # Embedding
            embedding = await self.embedding_service.embed(signature_text)

            # Interroger la base
            raw_results = self.profile_store.get_similar_profiles(
                query_embedding=embedding,
                top_k=top_k,
                threshold=threshold
            )

            # Agréger les résultats par mission_id (garder le meilleur score)
            for res in raw_results:
                mid = res["mission_id"]
                score = res["similarity"]  # 1 - distance
                if mid not in all_candidates or score > all_candidates[mid]["score"]:
                    all_candidates[mid] = {
                        "score": score,
                        "matched_signature": signature_text,
                        "action": sig.action,
                        "object": sig.object
                    }

        if not all_candidates:
            Logger.debug("[Retriever] Aucune mission similaire trouvée.")
            return []

        Logger.info(f"[Retriever] {len(all_candidates)} mission(s) candidate(s) après filtrage.")

        # Étape 2 : Récupérer les résumés pour chaque mission_id
        results = []
        for mission_id, data in all_candidates.items():
            episode = self.mission_store.get_episode(mission_id)
            if not episode:
                Logger.warning(f"[Retriever] Mission {mission_id} introuvable dans episodes.")
                continue

            # Extraire le résumé (presentator_result) ou fallback sur le goal
            summary = None
            presentator_raw = episode.get("presentator_result_json")
            if presentator_raw and presentator_raw not in ("{}", "null", ""):
                try:
                    import json
                    presentator_data = json.loads(presentator_raw)
                    if isinstance(presentator_data, dict) and presentator_data.get("status") == "success":
                        summary = episode.get("goal")  # fallback pour l'instant
                except Exception:
                    pass

            if not summary:
                summary = episode.get("goal") or "Mission sans résumé"

            results.append({
                "mission_id": mission_id,
                "goal": episode.get("goal"),
                "summary": summary,
                "score": data["score"],
                "matched_signature": data["matched_signature"],
                "action": data.get("action"),
                "object": data.get("object"),
                "episode": episode  # pour debug, à retirer en prod si nécessaire
            })

        # Trier par score décroissant
        results.sort(key=lambda x: x["score"], reverse=True)

        Logger.info(f"[Retriever] {len(results)} mission(s) retournée(s) avec résumé.")

        # À la fin de la méthode retrieve(), avant le return results
        # Ajouter un événement pour chaque résultat (ou un seul événement groupé)
        for result in results:
            Logger.event(
                "retriever_results",
                query_mission_id=query_mission_id,  # <--- mission qui interroge
                found_mission_id=result["mission_id"],  # <--- mission trouvée
                goal=result.get("goal"),
                score=result["score"],
                matched_signature=result.get("matched_signature"),
                top_k=self.top_k,
                threshold=self.threshold
            )
        return results

    async def retrieve_from_goal(
        self,
        goal: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Variante simplifiée : prend un texte de goal, le transforme en une seule signature
        (action: "accomplir", object: le goal) et appelle retrieve.

        Utile pour un premier test sans avoir d'Orchestrateur.
        """
        # Créer une signature artificielle
        sig = MissionSignature(
            action="accomplir",
            object=goal,
            desired_state=None
        )
        return await self.retrieve([sig], top_k, threshold)


# =====================================================
# FONCTION DE CONFORT (pour utilisation directe)
# =====================================================

async def retrieve_similar_missions(
    signatures: List[MissionSignature],
    top_k: int = RETRIEVAL_TOP_K,
    threshold: float = RETRIEVAL_THRESHOLD
) -> List[Dict[str, Any]]:
    """
    Fonction de confort pour appeler le Retriever sans instancier la classe.
    """
    retriever = Retriever(top_k=top_k, threshold=threshold)
    return await retriever.retrieve(signatures, top_k, threshold)
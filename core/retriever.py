"""
core/retriever.py
=================
Service de retrieval vectoriel des missions simples (MVP).
Mise à jour : utilise le résumé sémantique (summary) et filtre par modèle d'embedding actif.
             Utilise l'embedding_manager du runtime_state au lieu du singleton.
"""

import asyncio
from typing import List, Dict, Any, Optional, Set
from memory.mission_profile_store import MissionProfileStore
from memory.mission_store import MissionStore
from utils.logger import Logger
from core.plan_models import MissionSignature
from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD


class Retriever:
    def __init__(
        self,
        top_k: int = RETRIEVAL_TOP_K,
        threshold: float = RETRIEVAL_THRESHOLD,
        runtime_state = None
    ):
        self.top_k = top_k
        self.threshold = threshold
        self.profile_store = MissionProfileStore()
        self.mission_store = MissionStore()
        self.runtime_state = runtime_state

    async def retrieve(
        self,
        signatures: List[MissionSignature],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        query_mission_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
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

        # Récupération du modèle d'embedding actif depuis le runtime_state
        active_model = None
        embedding_manager = None
        if self.runtime_state:
            if hasattr(self.runtime_state, 'active_embedding_model'):
                active_model = self.runtime_state.active_embedding_model
                Logger.debug(f"[Retriever] Filtrage par modèle : {active_model}")
            if hasattr(self.runtime_state, 'embedding_manager'):
                embedding_manager = self.runtime_state.embedding_manager
                if not embedding_manager.active_provider:
                    Logger.warning("[Retriever] Aucun provider actif dans embedding_manager.")
                else:
                    Logger.debug(f"[Retriever] Embedding manager actif : {embedding_manager.active_provider_id}")

        # Fallback sur le service d'embedding legacy si le manager n'est pas disponible
        if not embedding_manager or not embedding_manager.active_provider:
            Logger.warning("[Retriever] Fallback sur le service d'embedding legacy.")
            from core.embedding_service import get_embedding_service
            legacy_service = get_embedding_service()

        all_candidates: Dict[str, Dict] = {}

        for sig in signatures:
            signature_text = f"{sig.action} {sig.object}"
            if sig.desired_state:
                signature_text += f" {sig.desired_state}"

            try:
                # Utilisation du manager ou fallback
                if embedding_manager and embedding_manager.active_provider:
                    embedding = await embedding_manager.embed(signature_text)
                else:
                    embedding = await legacy_service.embed(signature_text)
            except Exception as e:
                Logger.error(f"[Retriever] Erreur d'embedding pour '{signature_text}' : {e}")
                continue

            raw_results = self.profile_store.get_similar_profiles(
                query_embedding=embedding,
                top_k=top_k,
                threshold=threshold,
                embedding_model=active_model  # <-- FILTRAGE PAR MODÈLE
            )

            for res in raw_results:
                mid = res["mission_id"]
                score = res["similarity"]
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

        results = []
        for mission_id, data in all_candidates.items():
            episode = self.mission_store.get_episode(mission_id)
            if not episode:
                Logger.warning(f"[Retriever] Mission {mission_id} introuvable dans episodes.")
                continue

            # Utilisation du résumé sémantique, fallback sur le goal
            summary = episode.get("summary") or episode.get("goal") or "Mission sans résumé"

            results.append({
                "mission_id": mission_id,
                "goal": episode.get("goal"),
                "summary": summary,
                "score": data["score"],
                "matched_signature": data["matched_signature"],
                "action": data.get("action"),
                "object": data.get("object"),
                "episode": episode
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        Logger.info(f"[Retriever] {len(results)} mission(s) retournée(s) avec résumé.")

        for result in results:
            Logger.event(
                "retriever_results",
                query_mission_id=query_mission_id,
                found_mission_id=result["mission_id"],
                goal=result.get("goal"),
                summary=result.get("summary"),
                score=result["score"],
                matched_signature=result.get("matched_signature"),
                top_k=self.top_k,
                threshold=self.threshold,
                embedding_model=active_model
            )
        return results

    async def retrieve_from_goal(
        self,
        goal: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        sig = MissionSignature(
            action="accomplir",
            object=goal,
            desired_state=None
        )
        return await self.retrieve([sig], top_k, threshold)


async def retrieve_similar_missions(
    signatures: List[MissionSignature],
    top_k: int = RETRIEVAL_TOP_K,
    threshold: float = RETRIEVAL_THRESHOLD,
    runtime_state = None
) -> List[Dict[str, Any]]:
    retriever = Retriever(top_k=top_k, threshold=threshold, runtime_state=runtime_state)
    return await retriever.retrieve(signatures, top_k, threshold)
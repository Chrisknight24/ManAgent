"""
core/retriever.py
=================
Service de retrieval vectoriel des missions simples (MVP).
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

        all_candidates: Dict[str, Dict] = {}

        for sig in signatures:
            signature_text = f"{sig.action} {sig.object}"
            if sig.desired_state:
                signature_text += f" {sig.desired_state}"

            # --- NOUVEAU : gestion des erreurs d'embedding ---
            try:
                embedding = await self.embedding_service.embed(signature_text)
            except Exception as e:
                Logger.error(f"[Retriever] Erreur d'embedding pour '{signature_text}' : {e}")
                continue  # On ignore cette signature et on passe à la suivante

            raw_results = self.profile_store.get_similar_profiles(
                query_embedding=embedding,
                top_k=top_k,
                threshold=threshold
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

            summary = None
            presentator_raw = episode.get("presentator_result_json")
            if presentator_raw and presentator_raw not in ("{}", "null", ""):
                try:
                    import json
                    presentator_data = json.loads(presentator_raw)
                    if isinstance(presentator_data, dict) and presentator_data.get("status") == "success":
                        summary = episode.get("goal")
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
        sig = MissionSignature(
            action="accomplir",
            object=goal,
            desired_state=None
        )
        return await self.retrieve([sig], top_k, threshold)


async def retrieve_similar_missions(
    signatures: List[MissionSignature],
    top_k: int = RETRIEVAL_TOP_K,
    threshold: float = RETRIEVAL_THRESHOLD
) -> List[Dict[str, Any]]:
    retriever = Retriever(top_k=top_k, threshold=threshold)
    return await retriever.retrieve(signatures, top_k, threshold)
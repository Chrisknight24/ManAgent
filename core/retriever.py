# core/retriever.py
# Version avec support de root_mission_id

import asyncio
from typing import List, Dict, Any, Optional, Set
from memory.mission_profile_store import MissionProfileStore
from memory.mission_store import MissionStore
from utils.logger import Logger
from core.plan_models import MissionSignature
from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD
from core.cache import CacheManager

class Retriever:
    def __init__(
        self,
        top_k: int = RETRIEVAL_TOP_K,
        threshold: float = RETRIEVAL_THRESHOLD,
        runtime_state = None,
        cache_manager: Optional[CacheManager] = None
    ):
        if cache_manager is None:
            Logger.warning("[Retriever] Aucun cache_manager fourni, utilisation d'une instance locale (non partagée).")
            cache_manager = CacheManager()
        self.top_k = top_k
        self.threshold = threshold
        self.profile_store = MissionProfileStore()
        self.mission_store = MissionStore()
        self.runtime_state = runtime_state
        self.cache_manager = cache_manager
            
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

        # Normaliser les signatures pour la clé de cache
        normalized_sigs = []
        for s in signatures:
            normalized_sigs.append({
                "action": s.action.strip().lower(),
                "object": s.object.strip().lower(),
                "desired_state": s.desired_state.strip().lower() if s.desired_state else None
            })

        # Construire les paramètres de cache
        cache_params = {
            "signatures": normalized_sigs,
            "top_k": top_k,
            "threshold": threshold,
            "embedding_model": self.runtime_state.active_embedding_model if self.runtime_state else None
        }

        # --- 1. Vérifier le cache ---
        cached = await self.cache_manager.get("retrieval", cache_params)
        if cached is not None:
            Logger.info(f"[Retriever] Cache hit : {len(cached)} résultats retournés.")
            return cached
        
        # --- 2. Exécuter le retrieval ---
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

        if not embedding_manager or not embedding_manager.active_provider:
            Logger.warning("[Retriever] Fallback sur le service d'embedding legacy.")
            from core.embedding_service import get_embedding_service
            legacy_service = get_embedding_service()

        all_candidates: Dict[str, Dict] = {}

        for sig in signatures:
            signature_text = f"{sig.action} {sig.object}"
            
            try:
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
                embedding_model=active_model
            )
            Logger.debug(f"[Retriever] {len(raw_results)} résultats bruts pour '{signature_text}'")

            for res in raw_results:
                Logger.debug(f"[Retriever]   {res['mission_id']} -> distance={res['distance']:.4f}, score={res['similarity']:.4f}")
                mid = res["mission_id"]
                score = res["similarity"]
                if mid not in all_candidates or score > all_candidates[mid]["score"]:
                    all_candidates[mid] = {
                        "score": score,
                        "matched_signature": signature_text,
                        "action": sig.action,
                        "object": sig.object,
                        "root_mission_id": res.get("root_mission_id")  # <-- ON STOCKE LE root_mission_id
                    }

        if not all_candidates:
            Logger.debug("[Retriever] Aucune mission similaire trouvée.")
            return []

        Logger.info(f"[Retriever] {len(all_candidates)} mission(s) candidate(s) après filtrage.")

        results = []
        for mission_id, data in all_candidates.items():
            # Récupérer le root_mission_id depuis le profile
            root_mission_id = data.get("root_mission_id") or mission_id

            # Charger l'épisode depuis le root_mission_id (pour avoir le résumé global)
            episode = self.mission_store.get_episode(root_mission_id)
            if not episode:
                Logger.warning(f"[Retriever] Mission racine {root_mission_id} (issue de {mission_id}) introuvable dans episodes.")
                # Fallback sur l'ID du profile
                episode = self.mission_store.get_episode(mission_id)
                if not episode:
                    Logger.warning(f"[Retriever] Mission {mission_id} introuvable dans episodes (fallback échoué).")
                    continue

            summary = episode.get("summary") or episode.get("goal") or "Mission sans résumé"

            results.append({
                "mission_id": root_mission_id,  # on retourne l'ID racine
                "source_profile_id": mission_id,  # pour traçabilité
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

        # --- 3. Stocker les résultats dans le cache ---
        if results:
            await self.cache_manager.set(
                "retrieval",
                cache_params,
                results,
                invalidation_markers=self.cache_manager._normalize_signatures(
                    [{"action": s.action, "object": s.object} for s in signatures]
                )
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
    runtime_state = None,
    cache_manager: Optional[CacheManager] = None
) -> List[Dict[str, Any]]:
    if cache_manager is None:
        cache_manager = CacheManager()
    retriever = Retriever(
        top_k=top_k,
        threshold=threshold,
        runtime_state=runtime_state,
        cache_manager=cache_manager
    )
    return await retriever.retrieve(signatures, top_k, threshold)
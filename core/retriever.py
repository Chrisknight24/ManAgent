# core/retriever.py
# Version avec support de root_mission_id et SkillRegistry

import asyncio
import time
from typing import List, Dict, Any, Optional, Set
try:
    from memory.mission_profile_store import MissionProfileStore
except Exception:
    class MissionProfileStore:
        pass

try:
    from memory.mission_store import MissionStore
except Exception:
    class MissionStore:
        pass

from utils.logger import Logger
try:
    from core.plan_models import MissionSignature
except Exception:
    class MissionSignature:
        pass

from core.constants import RETRIEVAL_TOP_K, RETRIEVAL_THRESHOLD
from core.cache import CacheManager
from core.skills.registry import SkillRegistry
from core.skills.models import SkillManifest, SkillVersion, ExecutionEnvironment

class Retriever:
    def __init__(
        self,
        top_k: int = RETRIEVAL_TOP_K,
        threshold: float = RETRIEVAL_THRESHOLD,
        runtime_state = None,
        cache_manager: Optional[CacheManager] = None,
        skill_registry: Optional[SkillRegistry] = None
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
        self.skill_registry = skill_registry or SkillRegistry()
            
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

        # Identifier la mission racine et le solver_id
        solver_id = query_mission_id
        mission_id = None
        if self.runtime_state:
            mission_id = getattr(self.runtime_state, "current_mission_id", None)

        Logger.event(
            "retriever_query",
            query_mission_id=query_mission_id,
            solver_id=solver_id,
            mission_id=mission_id,
            signatures=[{"action": s.action, "object": s.object, "desired_state": s.desired_state} for s in signatures],
            top_k=self.top_k,
            threshold=self.threshold
        )

        top_k = top_k or self.top_k
        threshold = threshold or self.threshold

        normalized_sigs = []
        for s in signatures:
            normalized_sigs.append({
                "action": s.action.strip().lower(),
                "object": s.object.strip().lower(),
                "desired_state": s.desired_state.strip().lower() if s.desired_state else None
            })

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
                        "root_mission_id": res.get("root_mission_id")
                    }

        if not all_candidates:
            Logger.debug("[Retriever] Aucune mission similaire trouvée.")
            return []

        Logger.info(f"[Retriever] {len(all_candidates)} mission(s) candidate(s) après filtrage.")

        results = []
        for mission_id, data in all_candidates.items():
            root_mission_id = data.get("root_mission_id") or mission_id
            episode = self.mission_store.get_episode(root_mission_id)
            if not episode:
                Logger.warning(f"[Retriever] Mission racine {root_mission_id} (issue de {mission_id}) introuvable dans episodes.")
                episode = self.mission_store.get_episode(mission_id)
                if not episode:
                    Logger.warning(f"[Retriever] Mission {mission_id} introuvable dans episodes (fallback échoué).")
                    continue

            summary = episode.get("summary") or episode.get("goal") or "Mission sans résumé"

            # Calcul du score hybride (70% similarité vectorielle + 30% récence temporelle)
            ep_time_raw = episode.get("timestamp") or episode.get("created_at") or 0.0
            ep_time = 0.0
            if isinstance(ep_time_raw, (int, float)):
                ep_time = float(ep_time_raw)
            elif isinstance(ep_time_raw, str):
                try:
                    ep_time = float(ep_time_raw)
                except ValueError:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(ep_time_raw.replace('Z', '+00:00'))
                        ep_time = dt.timestamp()
                    except Exception:
                        ep_time = 0.0

            now = time.time()
            age_hours = max(0.0, (now - ep_time) / 3600.0) if ep_time > 0 else 1000.0
            recency_score = 1.0 / (1.0 + (age_hours / 24.0))  # Décroissance douce sur 24h
            composite_score = (data["score"] * 0.7) + (recency_score * 0.3)

            results.append({
                "mission_id": root_mission_id,
                "source_profile_id": mission_id,
                "goal": episode.get("goal"),
                "summary": summary,
                "score": data["score"],
                "composite_score": composite_score,
                "matched_signature": data["matched_signature"],
                "action": data.get("action"),
                "object": data.get("object"),
                "episode": episode
            })

        results.sort(key=lambda x: x.get("composite_score", x["score"]), reverse=True)
        Logger.info(f"[Retriever] {len(results)} mission(s) retournée(s) avec résumé.")

        Logger.event(
            "retriever_search_completed",
            query_mission_id=query_mission_id,
            solver_id=solver_id,
            mission_id=query_mission_id or mission_id,
            results=results,
            top_k=self.top_k,
            threshold=self.threshold,
            embedding_model=active_model
        )

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

    async def retrieve_skills(
        self,
        signatures: List[MissionSignature],
        environment: Optional[ExecutionEnvironment] = None,
        only_production: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Pré-filtre ultra-rapide (<2ms) des Skills éligibles depuis le SkillRegistry
        à partir des signatures causales et des contraintes d'environnement.
        Renvoie la liste des manifests et versions sous un format structuré pour le Solver/Planner.
        """
        if not signatures:
            return []

        # Construction des hashes de signature
        sig_hashes = [s.to_hash() for s in signatures if hasattr(s, "to_hash")]
        if not sig_hashes:
            for s in signatures:
                act = getattr(s, "action", "").strip().lower()
                obj = getattr(s, "object", "").strip().lower()
                sig_hashes.append(f"sig:{act}:{obj}")

        # Support dictionary or ExecutionEnvironment object for host_environment
        host_env_dict = environment.to_dict() if hasattr(environment, "to_dict") else environment

        candidates = self.skill_registry.find_candidates_by_signatures(
            signature_hashes=sig_hashes,
            host_environment=host_env_dict,
            only_production=only_production
        )

        results: List[Dict[str, Any]] = []
        for manifest, version in candidates:
            score = version.trust_profile.trust_score if hasattr(version.trust_profile, "trust_score") else 1.0
            results.append({
                "skill_id": manifest.skill_id,
                "name": manifest.name,
                "description": manifest.description,
                "version": version.version,
                "state": version.state.value if hasattr(version.state, "value") else str(version.state),
                "parameters_schema": manifest.parameters_schema,
                "checkpoints": [cp.name for cp in manifest.checkpoints],
                "trust_score": score,
                "flow_payload_ref": version.flow_payload_ref,
                "manifest": manifest,
                "version_obj": version,
            })

        Logger.debug(f"[Retriever] {len(results)} Skill(s) éligible(s) trouvé(s) pour {len(sig_hashes)} signature(s).")
        return results


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

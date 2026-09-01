"""
provider_manager.py
===================
Manager central des providers IA avec résilience multi-clés et routage cognitif par Carte d'Identité.
"""

from typing import List, AsyncGenerator, Type, Any, Optional, Dict, Tuple
from pydantic import BaseModel, Field
import asyncio
from providers.base_provider import BaseProvider, ProviderQuotaExhaustedError, ProviderError
from utils.logger import Logger
from core.i18n import _


class ModelMetadata(BaseModel):
    """
    Carte d'Identité formelle d'un modèle IA (Model Identity Card).
    Les scores (reasoning, speed, benchmark) utilisent une échelle ouverte (>= 0.0).
    """
    model_id: str
    provider_id: str
    display_name: str
    capabilities: List[str] = Field(default_factory=list) # tool_calling, structured_output, vision, streaming, system_instructions
    reasoning_score: float = 1.0     # Capacité de raisonnement logique (échelle ouverte >= 0.0)
    speed_score: float = 1.0         # Vitesse d'exécution/latence (échelle ouverte >= 0.0)
    cost_tier: str = "standard"      # "free", "free_tier_available", "ultra_low", "standard", "premium"
    benchmark_score: float = 50.0    # Score d'intelligence globale (échelle ouverte >= 0.0)
    latency_profile: str = "medium"  # "ultra_low", "low", "medium", "high"
    context_window: int = 4000
    is_recommended: bool = False

    def __init__(self, **data):
        # Rétrocompatibilité avec les anciens schémas ayant reasoning_level
        if "reasoning_level" in data and "reasoning_score" not in data:
            data["reasoning_score"] = float(data.pop("reasoning_level", 1.0))
        super().__init__(**data)


class ModelRequirement(BaseModel):
    """
    Spécification minimale des besoins cognitifs exigés par une entité ou un micro-module.
    Fonctionne par seuils minimaux sans plafond artificiel.
    """
    role_name: str = "general" # "orchestrator", "planner", "solver", "presentator", "convergence", "compactor", "discovery", etc.
    min_reasoning_score: float = 0.0
    min_speed_score: float = 0.0
    min_benchmark_score: float = 0.0
    required_capabilities: List[str] = Field(default_factory=list) # e.g. ["tool_calling", "structured_output"]
    max_cost_tier: Optional[str] = None
    preferred_provider: Optional[str] = None
    preferred_model: Optional[str] = None
    allow_cross_provider_fallback: bool = True

    @classmethod
    def for_orchestrator(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="orchestrator",
            min_reasoning_score=3.0,
            min_benchmark_score=60.0,
            required_capabilities=["structured_output"],
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_planner(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="planner",
            min_reasoning_score=3.0,
            min_benchmark_score=60.0,
            required_capabilities=["structured_output"],
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_solver(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="solver",
            min_reasoning_score=2.5,
            min_benchmark_score=50.0,
            required_capabilities=["tool_calling"],
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_presentator(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="presentator",
            min_reasoning_score=2.0,
            min_speed_score=2.0,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_convergence(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="convergence",
            min_speed_score=3.0,
            min_reasoning_score=1.5,
            required_capabilities=["structured_output"],
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_discovery(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="discovery",
            min_speed_score=3.0,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )

    @classmethod
    def for_compactor(cls, preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> "ModelRequirement":
        return cls(
            role_name="compactor",
            min_speed_score=2.5,
            min_reasoning_score=2.0,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model
        )


class ProviderManager:

    def __init__(self):
        self.providers: List[BaseProvider] = []
        self.models_metadata: Dict[str, ModelMetadata] = {}
        self.routing_policy: Dict[str, Any] = {
            "allow_cross_provider": True,
            "mode": "auto",
            "fallback_order": ["gemini", "groq", "openai", "claude", "deepseek", "openrouter"],
            "cost_preference": "balanced",
            "role_mappings": {}
        }

    def set_routing_policy(self, policy: Dict[str, Any]):
        if isinstance(policy, dict):
            self.routing_policy.update(policy)
            Logger.info(f"[ProviderManager] Routing policy mise à jour : mode={self.routing_policy.get('mode')}, allow_cross={self.routing_policy.get('allow_cross_provider', True)}")

    def get_routing_policy(self) -> Dict[str, Any]:
        return self.routing_policy

    def register_model_metadata(self, metadata: ModelMetadata):
        self.models_metadata[metadata.model_id] = metadata
        Logger.info(
            f"Registered model metadata: {metadata.model_id} ({metadata.provider_id}) - "
            f"Reasoning: {metadata.reasoning_score}, Speed: {metadata.speed_score}, Capabilities: {metadata.capabilities}"
        )

    def get_model_metadata(self, model_id: str) -> Optional[ModelMetadata]:
        return self.models_metadata.get(model_id)

    def has_model_capability(self, model_id: str, capability: str) -> bool:
        meta = self.get_model_metadata(model_id)
        if meta:
            return capability in meta.capabilities
        return False

    def register_provider(self, provider: BaseProvider):
        self.providers.append(provider)
        Logger.info(f"Registered provider: {getattr(provider, 'provider_id', provider.provider_name)}")

    def _normalize_pid(self, pid: str) -> str:
        if not pid:
            return ""
        n = pid.lower().strip()
        if "gemini" in n or "google" in n:
            return "gemini"
        if "groq" in n:
            return "groq"
        if "openai" in n:
            return "openai"
        if "openrouter" in n:
            return "openrouter"
        if "claude" in n or "anthropic" in n:
            return "claude"
        if "deepseek" in n:
            return "deepseek"
        return n

    def get_provider(self, provider_id: str) -> Optional[BaseProvider]:
        if not provider_id:
            return None
        norm_id = self._normalize_pid(provider_id)
        for p in self.providers:
            pid = getattr(p, "provider_id", getattr(p, "provider_name", ""))
            if self._normalize_pid(pid) == norm_id:
                return p
        return None

    def find_best_model_for_requirement(
        self,
        requirement: ModelRequirement,
        exclude_models: Optional[List[str]] = None
    ) -> Optional[Tuple[str, str]]:
        """
        Algorithme d'élection de modèle :
        Trouve le meilleur (provider_id, model_id) respectant les exigences de la Carte d'Identité
        et les priorités utilisateur.
        """
        exclude_set = set(exclude_models or [])
        provider_priorities = [self._normalize_pid(p) for p in self.routing_policy.get(
            "fallback_order",
            ["gemini", "groq", "openai", "claude", "deepseek", "openrouter"]
        )]

        candidates: List[Tuple[float, str, str]] = [] # (score, provider_id, model_id)

        for model_id, meta in self.models_metadata.items():
            if model_id in exclude_set:
                continue

            # 1. Vérification que le provider est enregistré et possède des clés disponibles
            provider = self.get_provider(meta.provider_id)
            if not provider:
                continue
            if hasattr(provider, "has_available_keys") and not provider.has_available_keys():
                continue

            # 2. Filtrage strict des capacités requises
            missing_cap = False
            for cap in requirement.required_capabilities:
                if cap not in meta.capabilities:
                    missing_cap = True
                    break
            if missing_cap:
                continue

            # 3. Filtrage des seuils minimaux
            if meta.reasoning_score < requirement.min_reasoning_score:
                continue
            if meta.speed_score < requirement.min_speed_score:
                continue
            if meta.benchmark_score < requirement.min_benchmark_score:
                continue

            # 4. Calcul du score d'adéquation
            score = float(meta.benchmark_score)

            # Bonus selon le rôle de l'entité
            if requirement.min_speed_score > requirement.min_reasoning_score:
                # Tâche axée vitesse (ex: Convergence, Discovery)
                score += (meta.speed_score * 30.0) + (meta.reasoning_score * 5.0)
            else:
                # Tâche axée raisonnement (ex: Planner, Solver, Orchestrator)
                score += (meta.reasoning_score * 30.0) + (meta.speed_score * 5.0)

            # Bonus de priorité fournisseur de l'utilisateur
            norm_prov = self._normalize_pid(meta.provider_id)
            if norm_prov in provider_priorities:
                rank = provider_priorities.index(norm_prov)
                priority_bonus = max(0, 50 - (rank * 10))
                score += priority_bonus

            # Bonus si préféré explicitement
            if requirement.preferred_provider and norm_prov == self._normalize_pid(requirement.preferred_provider):
                score += 30.0
            if requirement.preferred_model and meta.model_id == requirement.preferred_model:
                score += 50.0

            # Bonus modèle recommandé par ManAgent
            if meta.is_recommended:
                score += 15.0

            # Bonus coût selon préférence
            cost_pref = self.routing_policy.get("cost_preference", "balanced")
            if cost_pref == "prefer_free" or cost_pref == "free_tier_available":
                if meta.cost_tier in ["free", "free_tier_available"]:
                    score += 25.0

            candidates.append((score, meta.provider_id, meta.model_id))

        if not candidates:
            return None

        # Tri décroissant par score
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0]
        Logger.info(f"[ProviderManager] Modèle élu pour rôle '{requirement.role_name}': {best[1]}/{best[2]} (Score: {best[0]:.1f})")
        return (best[1], best[2])

    async def initialize(self):
        Logger.info("Initializing provider manager")
        for provider in self.providers:
            try:
                Logger.info(f"Checking availability for: {getattr(provider, 'provider_id', 'Unknown')}")
                await provider.initialize()
            except Exception as e:
                Logger.error(f"Provider initialization failed: {getattr(provider, 'provider_id', 'Unknown')} -> {str(e)}")

    # =====================================================
    # STREAMING (Texte libre - Chat classique)
    # =====================================================
    async def stream_response(
        self,
        message: str,
        provider_id: str,
        model_id: str,
        context: list = None,
        tools: list = None
    ) -> AsyncGenerator[str | dict, None]:
        
        provider = self.get_provider(provider_id)
        if not provider:
            raise RuntimeError(_("Le provider '{}' est introuvable.").format(provider_id))

        provider.model_name = model_id
        async for chunk in provider.stream_response(message, context, tools):
            yield chunk

    async def generate_structured_output(
        self,
        prompt: str,
        provider_id: str,
        model_id: str,
        response_schema: Type[BaseModel],
        context: list = None,
        media_assets: Optional[list] = None
    ) -> BaseModel:
        """
        Génère une réponse strictement typée selon un schéma Pydantic.
        Retry sur les erreurs de validation (JSON/Pydantic).
        """
        from pydantic import ValidationError
        from json import JSONDecodeError

        provider = self.get_provider(provider_id)
        if not provider:
            raise RuntimeError(f"Le provider '{provider_id}' est introuvable.")

        provider.model_name = model_id
        
        max_retries = 3
        current_prompt = prompt

        for attempt in range(max_retries):
            try:
                return await provider.generate_structured_output(
                    prompt=current_prompt,
                    response_schema=response_schema,
                    context=context,
                    media_assets=media_assets
                )
                
            except (ValidationError, JSONDecodeError) as e:
                Logger.warning(f"[ProviderManager] Erreur de structuration ({provider_id}/{model_id}) - Tentative {attempt + 1}/{max_retries} : {str(e)}")
                
                if attempt == max_retries - 1:
                    error_msg = (
                        f"Le modèle '{model_id}' a échoué à générer une réponse conforme au schéma {response_schema.__name__} "
                        f"après {max_retries} tentatives. Structure attendue non respectée par le LLM."
                    )
                    Logger.error(f"[ProviderManager] ❌ {error_msg}")
                    raise RuntimeError(_("{error_msg} | Détail technique : {}").format(str(e), error_msg=error_msg)) from e
                
                error_feedback = (
                    _("\n\n--- ERREUR DE STRUCTURE (Tentative {}/{}) ---\n").format(attempt + 2, max_retries) +
                    _("Détail : {}\n").format(str(e)) +
                    _("Action : Ré-analyse la consigne et fournis uniquement un JSON valide respectant strictement le schéma requis.")
                )
                
                current_prompt = prompt + error_feedback
                await asyncio.sleep(0.5)
                
    def clear(self):
        self.providers.clear()
        self.models_metadata.clear()

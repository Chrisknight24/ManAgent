"""
llm.py
======
Interface unifiée représentant un moteur cognitif (Le Cerveau).
Version avec Progressive Disclosure via discovery_request imbriqué.
"""

from __future__ import annotations

import time
import uuid
import asyncio
from contextlib import nullcontext
from typing import (
    Type, AsyncGenerator, List, Dict, Optional, Union, Any,
    TYPE_CHECKING
)
from pydantic import BaseModel
from providers.provider_manager import ProviderManager, ModelRequirement
from providers.base_provider import ProviderQuotaExhaustedError, ProviderError
from utils.logger import Logger
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from pydantic import ValidationError
from core.constants import LLM_STRUCTURED_MAX_ATTEMPTS, LLM_DISCOVERY_MAX_ITERATIONS
if TYPE_CHECKING:
    from core.entity import Entity
    from core.discovery.data_provider import DataProvider
    from core.discovery.models import DiscoveryRequest, RefinedContext


class Llm:
    """
    Représente l'intelligence artificielle d'une Entité.
    Maintient son propre contexte de travail sans polluer le Provider partagé.
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        provider_id: str,
        model_id: str,
        system_prompt: str = "",
        runtime_state=None,
        requirement: Optional[ModelRequirement] = None
    ):
        self.provider_manager = provider_manager
        self.system_prompt = system_prompt
        self.runtime_state = runtime_state

        if requirement:
            self.requirement = requirement
        else:
            self.requirement = ModelRequirement(
                role_name="general",
                preferred_provider=provider_id if provider_id and provider_id != "auto" else None,
                preferred_model=model_id if model_id and model_id != "auto" else None
            )

        # Si le provider_id ou model_id est "auto", résoudre selon le ModelRequirement
        if not provider_id or provider_id == "auto" or not model_id or model_id == "auto":
            resolved = self.provider_manager.find_best_model_for_requirement(self.requirement)
            if resolved:
                self.provider_id, self.model_id = resolved
            else:
                self.provider_id = provider_id or "gemini"
                self.model_id = model_id or "gemini-2.5-flash"
        else:
            self.provider_id = provider_id
            self.model_id = model_id

        self.context: List[Dict[str, str]] = []

        # --- PROGRESSIVE DISCLOSURE ---
        self._discovery_enabled = False
        self._discovery_engine = None
        self._entity = None
        self._entity_id: Optional[str] = None
        self._entity_name: Optional[str] = None
        self._entity_role: Optional[str] = None
        self._prompt_loader = get_prompt_loader()
        self._max_iterations = LLM_DISCOVERY_MAX_ITERATIONS

        # --- NOUVEAU : contexte de données partagé ---
        self._data_context: Optional[Any] = None

        self._last_refined_context: Optional[RefinedContext] = None
        self._discovery_history = []  # historique des signatures demandées (global à l'instance)

    def _resolve_fallback(self, excluded_models: List[str]) -> bool:
        """
        Trouve et bascule vers un modèle de substitution Cross-Provider
        si l'exigence le permet et qu'un candidat éligible est disponible.
        """
        if not self.requirement or not self.requirement.allow_cross_provider_fallback:
            return False

        routing_pol = self.provider_manager.get_routing_policy()
        if not routing_pol.get("allow_cross_provider", True) and not routing_pol.get("auto_provider_fallback", True):
            return False

        if self.model_id not in excluded_models:
            excluded_models.append(self.model_id)

        best = self.provider_manager.find_best_model_for_requirement(self.requirement, exclude_models=excluded_models)
        if best:
            new_provider_id, new_model_id = best
            Logger.event(
                "llm_fallback_triggered",
                old_provider=self.provider_id,
                old_model=self.model_id,
                new_provider=new_provider_id,
                new_model=new_model_id,
                role=self.requirement.role_name
            )
            Logger.warning(
                f"[LLM Fallback] Basculement Cross-Provider ({self.requirement.role_name}): "
                f"{self.provider_id}/{self.model_id} -> {new_provider_id}/{new_model_id}"
            )
            self.provider_id = new_provider_id
            self.model_id = new_model_id
            return True
        return False

    def has_capability(self, capability: str) -> bool:
        """
        Interroge exclusivement la carte d'identité du modèle enregistrée pour savoir s'il supporte la capacité demandée.
        Aucune logique empirique locale ou devinement.
        """
        if self.provider_manager.get_model_metadata(self.model_id) is not None:
            return self.provider_manager.has_model_capability(self.model_id, capability)

        Logger.warning(f"RÉGULATION STRICTE : Le modèle '{self.model_id}' n'a pas de carte d'identité (ModelMetadata) enregistrée. Toutes ses capacités sont désactivées par défaut.")
        return False
    
    def enable_discovery(self, engine, entity: 'Entity') -> None:
        """
        Active la Progressive Disclosure pour ce LLM.
        Met à jour l'engine, l'entité, le contexte de données et logge les providers actifs.
        """
        self._discovery_enabled = True
        self._discovery_engine = engine
        self._entity = entity
        self._entity_id = entity.entity_id
        self._entity_name = getattr(entity, "name", None)
        self._entity_role = getattr(entity, "role", None)

        if hasattr(entity, 'get_data_context'):
            self.set_data_context(entity.get_data_context())
        else:
            self.set_data_context(entity)

        providers = entity.get_data_providers()
        Logger.info(
            _("[Llm] Discovery activé pour {entity_id} avec {count} provider(s).")
            .format(entity_id=self._entity_id, count=len(providers))
        )
        Logger.debug(f"[Llm] Providers actifs : {list(providers.keys())}")

    def _build_discovery_section(self, schema: Type[BaseModel], blocked_data_types: set = None) -> str:
        """
        Construit la section Progressive Disclosure à injecter dans le prompt.
        Retourne une chaîne vide si aucune donnée n'est disponible.
        Logs détaillés pour faciliter le diagnostic.
        """
        if blocked_data_types is None:
            blocked_data_types = set()
            
        if not self._discovery_engine:
            Logger.debug("[LLM] _build_discovery_section: _discovery_engine est None.")
            return ""
        if not self._entity:
            Logger.debug("[LLM] _build_discovery_section: _entity est None.")
            return ""

        try:
            providers = self._entity.get_data_providers()
            Logger.debug(f"[LLM] _build_discovery_section: providers trouvés = {list(providers.keys())}")

            data_types_info = {}
            for provider_name, provider in providers.items():
                try:
                    data_type = provider.get_data_type()
                    
                    if data_type in blocked_data_types:
                        Logger.debug(f"[LLM] _build_discovery_section: Type '{data_type}' est bloqué. Ignoré.")
                        continue
                        
                    explorer = self._discovery_engine.get_explorer(data_type)
                    if not explorer:
                        Logger.warning(
                            f"[LLM] Aucun Explorer enregistré pour le type '{data_type}' "
                            f"(provider: {provider_name}). Ignoré."
                        )
                        continue
                    scope = ""
                    if hasattr(explorer, "get_scope_description"):
                        scope = explorer.get_scope_description()
                    elif hasattr(provider, "get_scope_description"):
                        scope = provider.get_scope_description()

                    goals = explorer.get_available_goals()
                    targets = provider.get_targets()
                    data_types_info[provider_name] = {
                        "data_type": data_type,
                        "scope": scope,
                        "goals": goals,
                        "targets": targets
                    }
                    Logger.debug(
                        f"[LLM] Provider '{provider_name}' -> type={data_type}, "
                        f"goals={goals}, targets={len(targets)}"
                    )
                except Exception as e:
                    Logger.error(
                        f"[LLM] Erreur lors du traitement du provider '{provider_name}': {e}"
                    )
                    continue

            if not data_types_info:
                Logger.debug("[LLM] _build_discovery_section: aucun DataProvider exploitable trouvé.")
                return ""

            try:
                schema_desc = self._get_schema_description(schema)
            except Exception as e:
                Logger.error(f"[LLM] Erreur lors de la description du schéma: {e}")
                schema_desc = "Schéma non disponible"

            discovery_section = self._prompt_loader.load(
                "discovery_injection.md",
                lang=getattr(self.runtime_state, "language", "en"),
                schema_description=schema_desc,
                data_types_info=data_types_info
            )
            Logger.debug(
                f"[LLM] Section PD générée avec {len(data_types_info)} type(s) de données, "
                f"longueur={len(discovery_section)} caractères."
            )
            return discovery_section

        except Exception as e:
            Logger.error(f"[LLM] Erreur inattendue dans _build_discovery_section: {e}")
            return ""
            
    def update_discovery_providers(self, providers: Dict[str, 'DataProvider']) -> None:
        """Met à jour la liste des DataProviders (appelé après enregistrement)."""
        pass

    def set_system_prompt(self, prompt: str):
        self.system_prompt = prompt

    def set_context(self, context_messages: List[Dict[str, str]]):
        self.context = context_messages

    def add_to_context(self, role: str, content: str):
        self.context.append({"role": role, "content": content})

    def clear_context(self):
        self.context.clear()

    # =====================================================
    # GESTION DU CONTEXTE DE DONNÉES (PROGRESSIVE DISCLOSURE)
    # =====================================================

    def set_data_context(self, context: Any) -> None:
        """
        Définit le contexte de données que l'entité souhaite partager
        avec le Discovery Framework.
        """
        self._data_context = context
        self.clear_discovery_history()

    def get_data_context(self) -> Any:
        """Retourne le contexte de données partagé, ou None."""
        return self._data_context

    # =====================================================
    # MÉTHODES DE GÉNÉRATION
    # =====================================================

    async def generate_text(self, prompt: str, tag: Optional[str] = None) -> str:
        excluded_models: List[str] = []
        while True:
            provider = self.provider_manager.get_provider(self.provider_id)
            if not provider:
                if self._resolve_fallback(excluded_models):
                    continue
                raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

            full_context = self._build_full_context()
            context_str = "\n".join([msg["content"] for msg in full_context]) if full_context else ""
            full_prompt = f"{context_str}\n\n{prompt}" if context_str else prompt

            provider.model_name = self.model_id

            start_time = time.monotonic()
            call_epoch = getattr(self.runtime_state, "generation_epoch", 0) if self.runtime_state else 0
            try:
                response = await provider.generate_response(user_message=full_prompt)
                if self.runtime_state and (self.runtime_state.cancel_requested or getattr(self.runtime_state, "generation_epoch", 0) != call_epoch):
                    Logger.warning(f"[LLM] Résultat generate_text ignoré car la session/génération a été annulée (epoch {call_epoch} vs actuel {getattr(self.runtime_state, 'generation_epoch', 0)}).")
                    raise asyncio.CancelledError("L'appel LLM a été annulé.")
                duration_ms = int((time.monotonic() - start_time) * 1000)
                event_fields = {
                    "tag": tag or "generate_text",
                    "kind": "text",
                    "provider_id": self.provider_id,
                    "model_id": self.model_id,
                    "prompt": prompt,
                    "response": response,
                    "duration_ms": duration_ms,
                    "success": True
                }
                Logger.event("llm_call", **event_fields)
                return response
            except ProviderQuotaExhaustedError as qe:
                Logger.warning(f"[LLM Quota Exhausted] {self.provider_id}/{self.model_id}: {qe}")
                if self._resolve_fallback(excluded_models):
                    continue
                raise
            except Exception as e:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                event_fields = {
                    "tag": tag or "generate_text",
                    "kind": "text",
                    "provider_id": self.provider_id,
                    "model_id": self.model_id,
                    "prompt": prompt,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "duration_ms": duration_ms,
                    "success": False
                }
                Logger.event("llm_call", **event_fields)
                raise
        
    async def stream(
        self,
        prompt: str,
        tools: list = None,
        tag: Optional[str] = None
    ) -> AsyncGenerator[str | dict, None]:
        excluded_models: List[str] = []
        while True:
            provider = self.provider_manager.get_provider(self.provider_id)
            if not provider:
                if self._resolve_fallback(excluded_models):
                    continue
                raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

            ephemeral_context = self._build_full_context()
            ephemeral_context.append({"role": "user", "content": prompt})

            provider.model_name = self.model_id

            try:
                # Note: We must consume the generator completely or return it
                # Because we are yielding, if a quota error occurs *during* the stream
                # it's usually too late to silently failover without breaking the stream.
                # However, if it happens during the initial connection (before the first chunk),
                # we can catch it.
                # Actually, provider.stream or provider.stream_response returns an AsyncGenerator.
                # The exception might happen when we do `async for`.
                
                async def consume_and_yield():
                    async for chunk in provider.stream(
                        prompt=prompt,
                        context=ephemeral_context,
                        tools=tools or []
                    ):
                        yield chunk
                
                # We yield from it, but wait! We can't yield inside a retry loop easily if we already yielded some chunks.
                # Let's track if we have yielded anything.
                has_yielded = False
                async for chunk in provider.stream(
                    prompt=prompt,
                    context=ephemeral_context,
                    tools=tools or []
                ):
                    has_yielded = True
                    yield chunk
                
                return # Success, exit retry loop

            except ProviderQuotaExhaustedError as qe:
                if not has_yielded:
                    Logger.warning(f"[LLM Quota Exhausted] {self.provider_id}/{self.model_id}: {qe}")
                    if self._resolve_fallback(excluded_models):
                        continue
                raise

            except Exception as e:
                Logger.event(
                    "llm_call",
                    tag=tag or "stream",
                    kind="stream",
                    provider_id=self.provider_id,
                    model_id=self.model_id,
                    prompt=prompt,
                    error=str(e),
                    error_type=type(e).__name__,
                    success=False
                )
                raise

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        tag: Optional[str] = None,
        mission_id: Optional[str] = None,
        with_discovery: bool = True,
        media_assets: Optional[List[Any]] = None
    ) -> BaseModel:
        """
        Génère une réponse structurée selon un schéma Pydantic.
        Active la Progressive Disclosure si le schéma possède un champ 'discovery_request'
        (hérité via BaseDiscoverySchema ou défini explicitement).
        Gère automatiquement les boucles de découverte (redondance) et le dépassement d'itérations.
        """
        from core.base_schema import BaseDiscoverySchema

        # Vérifier si le schéma supporte la Progressive Disclosure
        has_discovery_inheritance = issubclass(schema, BaseDiscoverySchema)
        has_discovery_field = hasattr(schema, "model_fields") and "discovery_request" in schema.model_fields

        if not (with_discovery and (has_discovery_inheritance or has_discovery_field)):
            Logger.debug(f"[LLM] Schéma {schema.__name__} (PD active: {with_discovery}) -> Mode legacy.")
            return await self._generate_structured_legacy(prompt, schema, tag, mission_id, media_assets=media_assets)

        discovery_history = self._discovery_history
        self._last_discovery_signature = None
        blocked_data_types = set()
        discovery_blocked = False
        prompt_modified = prompt

        discovery_section = self._build_discovery_section(schema, blocked_data_types)

        iteration = 0
        while iteration < self._max_iterations:
            iteration += 1

            if discovery_blocked:
                full_prompt = prompt_modified + (
                    "\n\n⚠️ IMPORTANT: La Progressive Disclosure est DÉSACTIVÉE. "
                    "Vous NE DEVEZ PAS utiliser `discovery_request` (définissez `discovery_request=None`). "
                    "Répondez directement avec les données dont vous disposez."
                )
            else:
                full_prompt = (discovery_section + "\n\n" + prompt_modified) if discovery_section else prompt_modified

            result = await self._call_llm_with_schema(
                prompt=full_prompt,
                schema=schema,
                tag=tag,
                mission_id=mission_id,
                media_assets=media_assets
            )

            if hasattr(result, 'discovery_request') and result.discovery_request is not None:
                if discovery_blocked:
                    Logger.warning("[LLM] discovery_request ignorée car la découverte est bloquée.")
                    result.discovery_request = None
                    return result

                discovery_req = result.discovery_request
                
                if discovery_req.data_type in blocked_data_types:
                    Logger.warning(f"[LLM] LLM a tenté d'utiliser un outil bloqué : {discovery_req.data_type}")
                    system_msg = f"\n\n⚠️ L'outil '{discovery_req.data_type}' EST DÉSACTIVÉ. Ne l'utilisez plus."
                    prompt_modified = prompt_modified + system_msg
                    continue

                targets_part = ",".join(discovery_req.targets)
                goals_part = ",".join(discovery_req.technical_goals)
                current_signature = f"{discovery_req.data_type}:{targets_part}:{goals_part}"
                
                # Le blocage de redondance ne s'applique QU'EN CAS D'APPELS CONSECUTIFS IDENTIQUES
                if getattr(self, "_last_discovery_signature", None) == current_signature:
                    explorer = self._discovery_engine.get_explorer(discovery_req.data_type) if self._discovery_engine else None
                    if explorer and explorer.allow_successive_calls():
                        Logger.debug(f"[LLM] Signature '{current_signature}' identique à la précédente, mais l'outil autorise les appels successifs.")
                    else:
                        Logger.warning(
                            f"[LLM] Répétition consécutive détectée : signature redondante '{current_signature}'. "
                            f"Masquage temporaire de l'outil '{discovery_req.data_type}'."
                        )
                        blocked_data_types.add(discovery_req.data_type)
                        system_msg = (
                            f"\n\n⚠️ REJET DE REDONDANCE CONSECUTIVE : Vous avez tenté d'invoquer de manière consécutive la même signature '{current_signature}'. "
                            f"L'outil '{discovery_req.data_type}' a été désactivé pour cet axe. "
                            "Veuillez vous contenter des données actuellement disponibles ou utiliser un autre axe."
                        )
                        prompt_modified = prompt_modified + system_msg
                        discovery_section = self._build_discovery_section(schema, blocked_data_types)
                        continue

                discovery_history.append(current_signature)

                Logger.debug(
                    _("[LLM] Découverte demandée : data_type={data_type}, targets={targets}, technical_goals={technical_goals}")
                    .format(
                        data_type=discovery_req.data_type,
                        targets=", ".join(discovery_req.targets),
                        technical_goals=", ".join(discovery_req.technical_goals)
                    )
                )

                try:
                    refined = await self._execute_discovery(discovery_req)
                    self._last_refined_context = refined
                    self._last_discovery_signature = current_signature
                    prompt_modified = prompt_modified + (
                        f"\n\n[RÉSULTAT DE L'INVESTIGATION - {current_signature}]\n{refined.summary}\n\n"
                        f"⚠️ INVESTIGATION EFFECTUÉE : Vous venez d'explorer '{current_signature}'. "
                        f"Si vous réitérez immédiatement cette MÊME signature à l'étape suivante sans modifier l'axe ou la cible, "
                        f"la demande sera rejetée et l'outil sera désactivé. Veuillez exploiter les données fournies ou explorer un autre axe."
                    )
                except Exception as e:
                    error_msg = f"⚠️ Erreur lors de l'investigation : {str(e)}. Veuillez répondre avec les données disponibles."
                    prompt_modified = prompt_modified + f"\n\n[ERREUR D'INVESTIGATION]\n{error_msg}"
                    Logger.error(f"[LLM] Erreur lors de l'exécution de la découverte : {e}")
                continue

            Logger.debug(_("[LLM] Réponse finale reçue (type: {schema})").format(schema=schema.__name__))
            return result

        # --- SECOURS DE SÉCURITÉ GARANTI : Jamais de return None ---
        Logger.warning(
            f"[LLM] Sortie de boucle de découverte (max_iterations={self._max_iterations}, discovery_blocked={discovery_blocked}). "
            "Exécution de la tentative finale sans Progressive Disclosure."
        )
        final_prompt = prompt_modified + (
            "\n\n⚠️ La phase d'investigation est terminée. "
            "Vous devez fournir votre décision finale immédiatement. "
            "Ne remplissez pas 'discovery_request' (définissez-le à null/None)."
        )
        try:
            final_result = await self._generate_structured_legacy(
                prompt=final_prompt,
                schema=schema,
                tag=tag,
                mission_id=mission_id,
                media_assets=media_assets
            )
            if hasattr(final_result, 'discovery_request'):
                final_result.discovery_request = None
            return final_result
        except Exception as e:
            Logger.error(f"[LLM] Échec de la tentative finale de secours : {e}")
            raise RuntimeError(
                _("Nombre maximum d'itérations ({max_iterations}) atteint sans réponse finale, "
                "et la dernière tentative a échoué.")
                .format(max_iterations=self._max_iterations)
            ) from e
            
    def clear_discovery_history(self):
        self._discovery_history = []
        Logger.debug("[LLM] Historique des découvertes réinitialisé.")

    def set_discovery_history(self, history: List[str]) -> None:
        """Définit l'historique des signatures pour la session en cours."""
        self._discovery_history = history or []
        Logger.debug(f"[LLM] Historique des signatures défini : {len(self._discovery_history)} entrée(s).")
        
    def _build_prompt_without_pd(self, base_prompt: str, discovery_blocked: bool = True) -> str:
        if not discovery_blocked:
            return base_prompt
        return base_prompt

    def get_last_refined_context(self) -> Optional[RefinedContext]:
        """Retourne le dernier RefinedContext généré par une Progressive Disclosure, puis le réinitialise."""
        ctx = self._last_refined_context
        self._last_refined_context = None
        return ctx

    # =====================================================
    # MÉTHODES INTERNES
    # =====================================================

    def _build_full_context(self) -> List[Dict[str, str]]:
        full_context = []
        if self.system_prompt:
            full_context.append({"role": "system", "content": self.system_prompt})
        full_context.extend(self.context)
        return full_context

    def _emit_llm_event(self, tag: str, prompt: str, response: Optional[BaseModel] = None,
                    error: Optional[Exception] = None, duration_ms: Optional[int] = None,
                    mission_id: Optional[str] = None, schema_name: Optional[str] = None,
                    context: Optional[List[Dict]] = None, kind: str = "structured"):
        event_fields = {
            "tag": tag or "llm_call",
            "kind": kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "prompt": prompt,
        }
        if schema_name:
            event_fields["schema"] = schema_name
        if response is not None:
            event_fields["response"] = response.model_dump(mode='json')
        if error is not None:
            event_fields["error"] = str(error)
            event_fields["error_type"] = type(error).__name__
            event_fields["success"] = False
        else:
            event_fields["success"] = True
        if duration_ms is not None:
            event_fields["duration_ms"] = duration_ms

        if mission_id is None and self.runtime_state:
            exec_ctx = getattr(self.runtime_state, 'execution_context', None)
            if exec_ctx:
                mission_id = exec_ctx.get('mission_id')
        if mission_id is not None:
            event_fields["mission_id"] = mission_id

        if context is not None:
            event_fields["context"] = context

        if self.runtime_state:
            exec_ctx = getattr(self.runtime_state, 'execution_context', {})
            if exec_ctx:
                solver_id = exec_ctx.get("solver_id")
                if solver_id:
                    event_fields["solver_id"] = solver_id
                attempt_num = exec_ctx.get("attempt_number")
                if attempt_num is not None:
                    event_fields["attempt_number"] = attempt_num
                step_id = exec_ctx.get("step_id")
                if step_id:
                    event_fields["step_id"] = step_id

        Logger.event("llm_call", **event_fields)
        
    async def _generate_structured_legacy(
        self,
        prompt: str,
        schema: Type[BaseModel],
        tag: Optional[str] = None,
        mission_id: Optional[str] = None,
        media_assets: Optional[List[Any]] = None
    ) -> BaseModel:
        provider = self.provider_manager.get_provider(self.provider_id)
        if not provider:
            raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

        ephemeral_context = self._build_full_context()
        ephemeral_context.append({"role": "user", "content": prompt})

        provider.model_name = self.model_id

        start_time = time.monotonic()
        try:
            result = await provider.generate_structured_output(
                prompt=prompt,
                response_schema=schema,
                context=ephemeral_context,
                media_assets=media_assets
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            self._emit_llm_event(
                tag=tag or schema.__name__,
                prompt=prompt,
                response=result,
                duration_ms=duration_ms,
                mission_id=mission_id,
                schema_name=schema.__name__,
                context=ephemeral_context,
                kind="structured"
            )
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            self._emit_llm_event(
                tag=tag or schema.__name__,
                prompt=prompt,
                error=e,
                duration_ms=duration_ms,
                mission_id=mission_id,
                schema_name=schema.__name__,
                context=ephemeral_context,
                kind="structured"
            )
            raise

    async def _call_llm_with_schema(
        self,
        prompt: str,
        schema: Type[BaseModel],
        tag: Optional[str] = None,
        mission_id: Optional[str] = None,
        media_assets: Optional[List[Any]] = None
    ) -> BaseModel:
        from pydantic import ValidationError
        max_attempts = LLM_STRUCTURED_MAX_ATTEMPTS
        excluded_models: List[str] = []

        for attempt in range(max_attempts):
            try:
                provider = self.provider_manager.get_provider(self.provider_id)
                if not provider:
                    if self._resolve_fallback(excluded_models):
                        continue
                    raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

                ephemeral_context = self._build_full_context()
                ephemeral_context.append({"role": "user", "content": prompt})

                provider.model_name = self.model_id

                start_time = time.monotonic()
                call_epoch = getattr(self.runtime_state, "generation_epoch", 0) if self.runtime_state else 0
                result = await provider.generate_structured_output(
                    prompt=prompt,
                    response_schema=schema,
                    context=ephemeral_context,
                    media_assets=media_assets
                )
                if self.runtime_state and (self.runtime_state.cancel_requested or getattr(self.runtime_state, "generation_epoch", 0) != call_epoch):
                    Logger.warning(f"[LLM] Résultat generate_structured ignoré car la session/génération a été annulée (epoch {call_epoch} vs actuel {getattr(self.runtime_state, 'generation_epoch', 0)}).")
                    raise asyncio.CancelledError("L'appel LLM structuré a été annulé.")
                duration_ms = int((time.monotonic() - start_time) * 1000)
                self._emit_llm_event(
                    tag=tag or schema.__name__,
                    prompt=prompt,
                    response=result,
                    duration_ms=duration_ms,
                    mission_id=mission_id,
                    schema_name=schema.__name__,
                    context=ephemeral_context,
                    kind="structured"
                )
                return result

            except ProviderQuotaExhaustedError as qe:
                Logger.warning(f"[LLM Quota Exhausted] {self.provider_id}/{self.model_id}: {qe}")
                if self._resolve_fallback(excluded_models):
                    continue
                raise

            except ValidationError as e:
                error_str = str(e)
                if "input_value=None" in error_str or "input_type=NoneType" in error_str:
                    Logger.error(f"[LLM] Le modèle a renvoyé une réponse vide (potentiel blocage de sécurité). Impossible de continuer.")
                    raise RuntimeError("Le modèle a renvoyé une réponse vide (bloquée par sécurité ou erreur API).")
                
                Logger.warning(
                    f"[LLM] Validation Pydantic échouée (tentative {attempt+1}/{max_attempts}) pour "
                    f"le schéma {schema.__name__} : {e}"
                )
                if attempt == max_attempts - 1:
                    raise

                error_details = "\n".join([
                    f"- Champ '{'.'.join(str(loc) for loc in err.get('loc', []))}' : {err.get('msg', '')}"
                    for err in e.errors()
                ])

                prompt = (
                    f"{prompt}\n\n"
                    f"⚠️ Votre réponse précédente n'a pas passé la validation.\n"
                    f"Erreurs détectées :\n{error_details}\n\n"
                    f"Veuillez corriger votre réponse en respectant strictement le format et les règles.\n"
                    f"Répondez uniquement avec le JSON valide, sans commentaire."
                )
                Logger.debug(f"[LLM] Nouveau prompt envoyé après erreur de validation :\n{prompt[:500]}...")
                continue

            except Exception as e:
                err_msg = str(e)
                if attempt < max_attempts - 1 and (
                    "Expecting value" in err_msg
                    or "JSONDecodeError" in type(e).__name__
                    or "line 1 column 1" in err_msg
                    or "empty" in err_msg.lower()
                    or "timeout" in err_msg.lower()
                ):
                    Logger.warning(
                        f"[LLM] Erreur transitoire/réponse vide reçue du provider (tentative {attempt+1}/{max_attempts}) "
                        f"pour le schéma {schema.__name__} : {e}. Nouvelle tentative après pause..."
                    )
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue

                Logger.error(f"[LLM] Erreur lors de l'appel LLM avec schéma {schema.__name__} : {e}")
                raise
            
    async def _execute_discovery(self, discovery_req: 'DiscoveryRequest') -> 'RefinedContext':
        if not self._discovery_engine:
            raise RuntimeError(_("Discovery activé mais _discovery_engine est None."))

        explorer = self._discovery_engine.get_explorer(discovery_req.data_type)
        if explorer is None:
            raise ValueError(_("Aucun Explorer enregistré pour le type '{data_type}'").format(
                data_type=discovery_req.data_type
            ))

        available_goals = explorer.get_available_goals()
        for goal in discovery_req.technical_goals:
            if goal not in available_goals:
                Logger.warning(
                    _("[LLM] Le goal technique '{technical_goal}' n'est pas explicitement dans get_available_goals pour {data_type}. L'Explorer tentera un repli.").format(
                        technical_goal=goal,
                        data_type=discovery_req.data_type
                    )
                )

        data_provider = self._entity.get_data_provider(discovery_req.data_type)

        run_id = uuid.uuid4().hex
        exec_ctx = getattr(self.runtime_state, 'execution_context', None)
        scope_cm = (
            exec_ctx.scope(
                discovery_run_id=run_id,
                entity_id=self._entity_id,
                entity_name=self._entity_name,
                entity_role=self._entity_role,
            )
            if exec_ctx is not None
            else nullcontext()
        )

        with scope_cm:
            plan = await explorer.generate_plan(
                goal=discovery_req.goal,
                llm=self,
                data_provider=data_provider,
                data_context=self._data_context,
                targets=discovery_req.targets,
                technical_goals=discovery_req.technical_goals
            )

            refined = await self._discovery_engine.start_discovery(
                entity_id=self._entity_id,
                plan=plan,
                llm=self,
                data_provider=data_provider,
                data_context=self._data_context,
                run_id=run_id,
                entity_name=self._entity_name,
                entity_role=self._entity_role,
            )

        return refined

    def _get_schema_description(self, schema: Type[BaseModel]) -> str:
        json_schema = schema.model_json_schema()
        lines = []
        properties = json_schema.get("properties", {})
        required = json_schema.get("required", [])
        for prop_name, prop_schema in properties.items():
            if prop_name == "discovery_request":
                lines.append(
                    f"- `discovery_request` (object) **optionnel**: "
                    f"Remplissez ce champ UNIQUEMENT si vous avez besoin d'investiguer une donnée."
                )
                continue
            prop_type = prop_schema.get("type", "any")
            prop_desc = prop_schema.get("description", "")
            required_marker = _("**requis**") if prop_name in required else _("optionnel")
            lines.append(f"- `{prop_name}` ({prop_type}) {required_marker}: {prop_desc}")
        return "\n".join(lines)

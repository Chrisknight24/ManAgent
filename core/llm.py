"""
llm.py
======
Interface unifiée représentant un moteur cognitif (Le Cerveau).
Version avec Progressive Disclosure via discovery_request imbriqué.
"""

from __future__ import annotations

import time
from typing import (
    Type, AsyncGenerator, List, Dict, Optional, Union, Any,
    TYPE_CHECKING
)
from pydantic import BaseModel
from providers.provider_manager import ProviderManager
from utils.logger import Logger
from core.prompt_loader import get_prompt_loader
from core.i18n import _

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
        runtime_state=None
    ):
        self.provider_manager = provider_manager
        self.provider_id = provider_id
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.runtime_state = runtime_state

        self.context: List[Dict[str, str]] = []

        # --- PROGRESSIVE DISCLOSURE ---
        self._discovery_enabled = False
        self._discovery_engine = None
        self._entity = None
        self._entity_id: Optional[str] = None
        self._prompt_loader = get_prompt_loader()
        self._max_iterations = 5

        # --- NOUVEAU : contexte de données partagé ---
        self._data_context: Optional[Any] = None

    # =====================================================
    # MÉTHODES PUBLIQUES
    # =====================================================

    def enable_discovery(self, engine, entity: 'Entity') -> None:
        self._discovery_enabled = True
        self._discovery_engine = engine
        self._entity = entity
        self._entity_id = entity.entity_id

        # Définir le contexte de données à partir de l'entité
        if hasattr(entity, 'get_data_context'):
            self.set_data_context(entity.get_data_context())
        else:
            self.set_data_context(entity)

        Logger.info(
            _("[Llm] Discovery activé pour {entity_id} avec {count} provider(s).")
            .format(entity_id=self._entity_id, count=len(entity.get_data_providers()))
        )

    def update_discovery_providers(self, providers: Dict[str, 'DataProvider']) -> None:
        """Met à jour la liste des DataProviders (appelé après enregistrement)."""
        # Conservé pour compatibilité
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

    def get_data_context(self) -> Any:
        """Retourne le contexte de données partagé, ou None."""
        return self._data_context

    # =====================================================
    # MÉTHODES DE GÉNÉRATION
    # =====================================================

    async def generate_text(self, prompt: str, tag: Optional[str] = None) -> str:
        provider = self.provider_manager.get_provider(self.provider_id)
        if not provider:
            raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

        ephemeral_context = self._build_full_context()
        ephemeral_context.append({"role": "user", "content": prompt})

        provider.model_name = self.model_id

        start_time = time.monotonic()
        try:
            response = await provider.generate_text(
                prompt=prompt,
                context=ephemeral_context
            )
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
        provider = self.provider_manager.get_provider(self.provider_id)
        if not provider:
            raise RuntimeError(_("Provider {provider_id} introuvable.").format(provider_id=self.provider_id))

        ephemeral_context = self._build_full_context()
        ephemeral_context.append({"role": "user", "content": prompt})

        provider.model_name = self.model_id

        try:
            async for chunk in provider.stream(
                prompt=prompt,
                context=ephemeral_context,
                tools=tools or []
            ):
                yield chunk
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
        mission_id: Optional[str] = None
    ) -> BaseModel:
        """
        Point d'entrée principal pour la génération structurée.
        Gère la boucle de Progressive Discovery via discovery_request imbriqué.
        """
        if not self._discovery_enabled:
            return await self._generate_structured_legacy(prompt, schema, tag, mission_id)

        # --- MODE DISCOVERY ACTIVÉ ---
        from core.base_schema import BaseDiscoverySchema

        if not issubclass(schema, BaseDiscoverySchema):
            Logger.warning(
                _("[LLM] Le schéma {schema} n'hérite pas de BaseDiscoverySchema. "
                  "La Progressive Disclosure ne peut pas être utilisée.")
                .format(schema=schema.__name__)
            )
            return await self._generate_structured_legacy(prompt, schema, tag, mission_id)

        base_prompt = self._build_discovery_prompt(prompt, schema)
        full_prompt = base_prompt

        iteration = 0
        while iteration < self._max_iterations:
            iteration += 1

            result = await self._call_llm_with_schema(
                prompt=full_prompt,
                schema=schema,
                tag=tag,
                mission_id=mission_id
            )

            if hasattr(result, 'discovery_request') and result.discovery_request is not None:
                discovery_req = result.discovery_request
                Logger.debug(
                    _("[LLM] Découverte demandée : data_type={data_type}, target={target}, technical_goal={technical_goal}")
                    .format(
                        data_type=discovery_req.data_type,
                        target=discovery_req.target,
                        technical_goal=discovery_req.technical_goal
                    )
                )

                refined = await self._execute_discovery(discovery_req)

                full_prompt = base_prompt + f"\n\n[RÉSULTAT DE L'INVESTIGATION]\n{refined.summary}"
                continue

            Logger.debug(_("[LLM] Réponse finale reçue (type: {schema})").format(schema=schema.__name__))
            return result

        raise RuntimeError(
            _("Nombre maximum d'itérations ({max_iterations}) atteint sans réponse finale.")
            .format(max_iterations=self._max_iterations)
        )

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

        # --- NOUVEAU : propager le mission_id depuis le runtime_state si non fourni ---
        if mission_id is None and self.runtime_state:
            mission_id = getattr(self.runtime_state, 'current_mission_id', None)
        if mission_id is not None:
            event_fields["mission_id"] = mission_id

        if context is not None:
            event_fields["context"] = context

        # Ajouter solver_id, attempt_number, step_id depuis le contexte d'exécution
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
        mission_id: Optional[str] = None
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
                context=ephemeral_context
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
        mission_id: Optional[str] = None
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
                context=ephemeral_context
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            effective_tag = tag or schema.__name__
            self._emit_llm_event(
                tag=effective_tag,
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

    async def _execute_discovery(self, discovery_req: 'DiscoveryRequest') -> 'RefinedContext':
        from core.discovery.models import RefinedContext

        if not self._discovery_engine:
            raise RuntimeError(_("Discovery activé mais _discovery_engine est None."))

        explorer = self._discovery_engine.get_explorer(discovery_req.data_type)
        if explorer is None:
            raise ValueError(_("Aucun Explorer enregistré pour le type '{data_type}'").format(
                data_type=discovery_req.data_type
            ))

        available_goals = explorer.get_available_goals()
        if discovery_req.technical_goal not in available_goals:
            raise ValueError(
                _("Le goal technique '{technical_goal}' n'est pas supporté par l'Explorer {data_type}. Goals disponibles : {goals}")
                .format(
                    technical_goal=discovery_req.technical_goal,
                    data_type=discovery_req.data_type,
                    goals=", ".join(available_goals)
                )
            )

        data_provider = self._entity.get_data_provider(discovery_req.data_type)

        plan = await explorer.generate_plan(
            goal=discovery_req.goal,
            technical_goal=discovery_req.technical_goal,
            target=discovery_req.target,
            llm=self,
            data_provider=data_provider,
            data_context=self._data_context
        )

        refined = await self._discovery_engine.start_discovery(
            entity_id=self._entity_id,
            plan=plan,
            llm=self,
            data_provider=data_provider,
            data_context=self._data_context
        )

        return refined

    def _build_discovery_prompt(self, original_prompt: str, schema: Type[BaseModel]) -> str:
        from core.discovery.data_provider import DataProvider

        if not self._discovery_engine:
            raise RuntimeError(_("Impossible de construire le prompt Discovery : _discovery_engine est None."))
        if not self._entity:
            raise RuntimeError(_("Aucune entité associée au LLM pour construire le prompt."))

        schema_desc = self._get_schema_description(schema)

        data_types_info = {}
        for provider_name, provider in self._entity.get_data_providers().items():
            data_type = provider.get_data_type()
            explorer = self._discovery_engine.get_explorer(data_type)
            if not explorer:
                Logger.warning(_("Aucun Explorer pour le type '{data_type}' (provider: {provider_name})").format(
                    data_type=data_type, provider_name=provider_name
                ))
                continue
            goals = explorer.get_available_goals()
            targets = provider.get_targets()
            data_types_info[provider_name] = {
                "data_type": data_type,
                "goals": goals,
                "targets": targets
            }

        discovery_section = self._prompt_loader.load(
            "discovery_injection.md",
            lang=getattr(self.runtime_state, "language", "en"),
            schema_description=schema_desc,
            data_types_info=data_types_info
        )

        return original_prompt + "\n\n" + discovery_section

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
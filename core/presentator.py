"""
core/presentator.py
===================
Entité spécialisée dans la reformulation et la présentation des résultats de mission.
Prend le contexte technique brut et le registre pour en faire un rapport clair pour l'utilisateur.
Mise à jour v5 : héritage BaseDiscoverySchema, activation PD unique.
"""

import asyncio
from typing import Optional, Dict, Any, List
from pydantic import Field
from core.entity import Entity
from core.llm import Llm
from utils.logger import Logger
from core.prompt_loader import get_prompt_loader
from core.i18n import _
from core.discovery.data_provider import DataProvider
from core.base_schema import BaseDiscoverySchema


class PresentatorOutput(BaseDiscoverySchema):
    """Structure de retour du Presentator pour une mission."""
    user_report: str = Field(..., description="Message long et détaillé à destination de l'utilisateur.")
    summary: str = Field(..., description="Résumé court (1-2 phrases) de l'action clé menée et du résultat principal.")


# ============================================================
# DataProvider dynamique pour le Presentator (basé sur un registre externe)
# ============================================================
class PresentatorRegistryProvider(DataProvider):
    """
    DataProvider pour le Presentator – utilise un registre passé en paramètre.
    """

    def __init__(self, registry: dict):
        self._registry = registry

    def get_data_type(self) -> str:
        return "registry"

    def get_targets(self) -> List[str]:
        return list(self._registry.keys()) if self._registry else []

    def get_data(self, target: str) -> Any:
        return self._registry.get(target, {}).get("value")

    def get_metadata(self, target: str) -> Dict[str, Any]:
        info = self._registry.get(target, {})
        return {
            "description": info.get("description", _("Pas de description")),
            "source": info.get("source", _("Inconnu")),
            "timestamp": info.get("timestamp", "N/A")
        }


class Presentator(Entity):
    def __init__(self, provider_manager, runtime_state,
                 provider_id: str = None, model_id: str = None, llm: Optional[Llm] = None,
                 parent: Optional[Entity] = None):

        super().__init__(name="presentator", role="presenter", llm=llm, parent=parent)

        self.provider_manager = provider_manager
        self.runtime_state = runtime_state
        self._discovery_activated = False  # Flag pour activation unique

        if not self.llm and provider_id and model_id:
            self.llm = Llm(
                provider_manager=provider_manager,
                provider_id=provider_id,
                model_id=model_id,
                runtime_state=runtime_state
            )

        if not self.llm:
            raise ValueError(_("Presentator requires a Llm instance or provider/model identifiers."))

    # ============================================================
    # IMPLÉMENTATION DE LA MÉTHODE ABSTRAITE DE Entity
    # ============================================================

    async def process(self, *args, **kwargs) -> str:
        Logger.info("[Presentator] 🎤 Appel via process() – délégation vers generate_mission_output")
        goal = kwargs.get("goal", args[0] if args else "")
        final_context = kwargs.get("final_context", args[1] if len(args) > 1 else "")
        variable_registry = kwargs.get("variable_registry", args[2] if len(args) > 2 else {})
        accumulated_response = kwargs.get("accumulated_response", args[3] if len(args) > 3 else "")
        mission_status = kwargs.get("mission_status", "success")
        error_reason = kwargs.get("error_reason", None)

        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            mission_status=mission_status,
            error_reason=error_reason
        )
        return output.user_report

    # ============================================================
    # ACTIVATION UNIQUE DE LA PD
    # ============================================================

    def _ensure_discovery_activated(self, registry: dict) -> None:
        """Active la Progressive Disclosure une seule fois avec un DataProvider basé sur le registre."""
        if self._discovery_activated:
            return
        if not self.runtime_state.discovery_engine:
            return
        if self.llm._discovery_enabled:
            self._discovery_activated = True
            return

        registry_provider = PresentatorRegistryProvider(registry)
        self.register_data_provider("registry", registry_provider)
        self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
        self._discovery_activated = True
        Logger.info("[Presentator] Progressive Disclosure activée avec DataProvider dynamique.")

    # ============================================================
    # MÉTHODE STRUCTURÉE – AVEC VUE NORMALISÉE
    # ============================================================

    async def generate_mission_output(
        self,
        goal: str,
        final_context: str,
        variable_registry: dict,
        accumulated_response: str,
        mission_status: str,
        error_reason: Optional[str] = None,
        mission_id: Optional[str] = None,
    ) -> PresentatorOutput:
        Logger.info(f"[Presentator] 📝 Génération structurée (rapport + résumé) pour statut: {mission_status}")

        # --- Activation de la PD (une seule fois) ---
        self._ensure_discovery_activated(variable_registry)

        # Construire la vue normalisée du registre
        registry_metadata_view = self._build_registry_metadata_view(variable_registry)

        # Récupération du mood
        mood = None
        if hasattr(self.runtime_state, 'session_memory') and self.runtime_state.session_memory:
            mood = self.runtime_state.session_memory.context.mood

        detail_level = getattr(self.runtime_state, "presentator_detail_level", "brief")

        loader = get_prompt_loader()
        prompt = loader.load(
            "presentator_output.md",
            lang=self.runtime_state.language,
            goal=goal,
            final_context=final_context,
            variable_registry=registry_metadata_view,  # Vue normalisée
            accumulated_response=accumulated_response,
            mission_status=mission_status,
            error_reason=error_reason or "",
            session_mood=mood or "neutre",
            detail_level=detail_level
        )

        try:
            output: PresentatorOutput = await self.llm.generate_structured(
                prompt=prompt,
                schema=PresentatorOutput,
                tag="Presentator_output",
                mission_id=mission_id
            )
            Logger.info("[Presentator] ✅ Génération structurée réussie.")
            return output

        except asyncio.TimeoutError as e:
            Logger.error(f"[Presentator] ⏰ Timeout lors de la génération structurée : {e}")
            return self._build_fallback_output(goal, mission_status, error_reason, _("Timeout du service d'IA"))

        except Exception as e:
            Logger.error(f"[Presentator] ❌ Échec de la génération structurée : {e}")
            return self._build_fallback_output(goal, mission_status, error_reason, str(e))

    # ============================================================
    # MÉTHODE UTILITAIRE : Vue normalisée du registre
    # ============================================================

    def _build_registry_metadata_view(self, registry: dict) -> Dict[str, Any]:
        if not registry:
            return {}

        view = {}
        for key, info in registry.items():
            value = info.get("value")

            hint = _("(donnée cachée, accessible via Progressive Disclosure)")
            if isinstance(value, dict):
                keys = list(value.keys())[:3]
                hint = _("(objet JSON avec clés: {keys})").format(keys=", ".join(keys))
            elif isinstance(value, list):
                hint = _("(liste de {count} éléments)").format(count=len(value))
            elif isinstance(value, str):
                if len(value) > 100:
                    hint = _("(chaîne de {length} caractères)").format(length=len(value))
                else:
                    hint = _("(chaîne: {preview}...)").format(preview=value[:50])
            elif value is None:
                hint = _("(null)")
            else:
                hint = _("(type: {type})").format(type=type(value).__name__)

            view[key] = {
                "description": info.get("description", _("Pas de description")),
                "source": info.get("source", _("Inconnu")),
                "timestamp": info.get("timestamp", "N/A"),
                "type": self._get_type_string(value),
                "value_hint": hint,
            }
        return view

    def _get_type_string(self, value) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        return "unknown"

    # ============================================================
    # FALLBACK
    # ============================================================

    def _build_fallback_output(self, goal: str, mission_status: str, error_reason: Optional[str], error_detail: str) -> PresentatorOutput:
        if mission_status == "success":
            user_report = _("✅ Mission '{goal}' accomplie techniquement, mais le rapport détaillé n'a pas pu être généré en raison d'une indisponibilité du service d'IA (détail : {detail}). Veuillez consulter les logs pour plus d'informations.").format(goal=goal, detail=error_detail)
            summary = _("Mission '{goal}' : succès technique (rapport indisponible).").format(goal=goal)
        else:
            reason = error_reason or _("cause inconnue")
            user_report = _("❌ La mission '{goal}' a échoué. Raison : {reason}. Le rapport détaillé est indisponible (indisponibilité du service d'IA : {detail}).").format(goal=goal, reason=reason, detail=error_detail)
            summary = _("Mission '{goal}' : échec – {reason} (rapport indisponible).").format(goal=goal, reason=reason)

        Logger.warning(f"[Presentator] ⚠️ Fallback utilisé pour la mission '{goal}'. Raison : {error_detail}")
        return PresentatorOutput(user_report=user_report, summary=summary)

    # ============================================================
    # ANCIENNES MÉTHODES CONSERVÉES POUR COMPATIBILITÉ
    # ============================================================

    async def generate_mission_report(self, goal: str, final_context: str, variable_registry: dict, accumulated_response: str) -> str:
        Logger.info("[Presentator] 🎤 [LEGACY] Génération du rapport long uniquement.")
        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry=variable_registry,
            accumulated_response=accumulated_response,
            mission_status="success"
        )
        return output.user_report

    async def generate_error_report(self, goal: str, error_reason: str, final_context: str) -> str:
        Logger.info("[Presentator] 📝 [LEGACY] Génération du rapport d'échec long uniquement.")
        output = await self.generate_mission_output(
            goal=goal,
            final_context=final_context,
            variable_registry={},
            accumulated_response="",
            mission_status="failed",
            error_reason=error_reason
        )
        return output.user_report
"""
orchestrator.py
===============
Orchestrateur central du runtime (Architecture Hub & Spoke).
RÔLE (Le PDG) : Centre de validation, de routage des paquets C++ et Superviseur Suprême.
"""

import asyncio
import time
from providers.provider_manager import ProviderManager
from core.event_bus import EventBus
from core.runtime_state import RuntimeState
from core.constants import Events, Actions, Providers, OrchestratorMode

from memory.history import ConversationMemory
from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.openai_provider import OpenAIProvider
from providers.openrouter_provider import OpenRouterProvider

from transport.packet_models import RequestPacket, ResponsePacket, ErrorPacket
from utils.logger import Logger

# Nouveaux imports pour l'architecture HTN (Hierarchical Task Network)
from .supervisor import Supervisor
from .plan_models import Plan, ExecutionStatus, OrchestratorDecision
from .solver import Solver
from core.entity import Entity
from core.llm import Llm
from pydantic import ValidationError
from .presentator import Presentator
from typing import Optional, Dict, List, Tuple, Any

from core.prompt_loader import get_prompt_loader
from core.i18n import _

import uuid
import copy
from datetime import datetime
from memory.session_memory import SessionMemory, MissionCache
from memory.mission_store import MissionStore
from core.learner import Learner

import json
from memory.session_store import SessionStore

from core.embedding_service import get_embedding_service
from memory.mission_profile_store import MissionProfileStore

from core.markers.manager import MarkerManager
from memory.fingerprint_store import FingerprintStore

from tools.tools_manager import ToolsManager

from core.discovery.providers.mission_history_provider import MissionHistoryProvider
from core.discovery.explorers.mission_history_explorer import MissionHistoryExplorer

class Orchestrator(Supervisor, Entity):
    """
    Orchestrateur central – Point d'entrée unique du runtime.
    Gère le routage, la validation des plans, la supervision des Solvers,
    la persistance des sessions et l'observabilité.
    """

    def __init__(self, provider_manager: ProviderManager, event_bus: EventBus, runtime_state: RuntimeState):
        # Initialisation des parents (ordre : Supervisor d'abord)
        Supervisor.__init__(self)
        Entity.__init__(self, name="orchestrator", role="CEO", llm=None, parent=None)

        self.provider_manager = provider_manager
        self.event_bus = event_bus
        self.runtime_state = runtime_state
        self.memory = ConversationMemory()

        self.active_sessions = {}
        self.current_execution_context = {}
        self.root_solver = None

        self.pending_tool_calls = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.session_memories: Dict[str, SessionMemory] = {}
        self.mission_store = MissionStore()
        self.session_store = SessionStore()
        self.marker_manager = MarkerManager()
        self.fingerprint_store = FingerprintStore()

        # Observabilité structurée (Logger -> JSON)
        Logger.configure_json_sink("observability/events.jsonl")

    async def process(self, packet: RequestPacket):
        """Implémentation de la méthode abstraite de Entity."""
        return await self.handle_request(packet)

    # =====================================================
    # POINT D'ENTRÉE PRINCIPAL (Frontend -> Python)
    # =====================================================
    async def handle_request(self, packet: RequestPacket):
        """Routage des actions provenant du frontend."""
        Logger.info(f"Orchestrator received action: {packet.action}")
        await self.propagate_event(Events.REQUEST_RECEIVED, {"action": packet.action})

        try:
            if packet.action == Actions.RUNTIME_CONFIGURE:
                return await self._handle_runtime_configure(packet)
            elif packet.action == Actions.CHAT_SEND:
                return await self._handle_chat_send(packet)
            elif packet.action == Actions.TOOL_RESULT:
                return await self._handle_tool_result(packet)
            elif packet.action == Actions.CHAT_STOP:
                return await self._handle_chat_stop()
            elif packet.action == Actions.LEARNER_ANALYZE:
                if not self.runtime_state.learner:
                    return ErrorPacket(type="error", message="Learner non initialisé. Envoyez un message d'abord.")
                await self.propagate_event(Events.LEARNER_ANALYZE_STARTED, {})
                try:
                    force_reanalyze = bool(packet.payload.get("force", False)) if packet.payload else False
                    analyzed = await self.runtime_state.learner.analyze_all_episodes(force=force_reanalyze)
                except Exception as e:
                    Logger.error(f"[Orchestrator] Échec de l'analyse Learner : {e}")
                    return ErrorPacket(type="error", message=f"Échec analyse : {str(e)}")
                await self.propagate_event(Events.LEARNER_ANALYZE_FINISHED, {"count": analyzed})
                return ResponsePacket(type="response", status="success",
                                      payload={"message": f"Analyse terminée : {analyzed} épisodes traités."})
            elif packet.action == Actions.SESSION_DELETE:
                session_id = packet.payload.get("session_id")
                if session_id:
                    self.memory.clear_session(session_id)
                    if session_id in self.session_memories:
                        del self.session_memories[session_id]
                    await asyncio.to_thread(self.session_store.delete_session, session_id)
                    Logger.info(f"[Orchestrator] Session supprimée (RAM + base) : {session_id}")
                return ResponsePacket(type="response", status="success",
                                      payload={"message": _("Session purged")})
            else:
                return ErrorPacket(type="error", message=_("Unknown action: {}").format(packet.action))
        except Exception as e:
            Logger.error(f"Orchestrator critical error: {str(e)}")
            await self.propagate_event(Events.RUNTIME_ERROR, {"message": str(e)})
            return ErrorPacket(type="error", message=str(e))

    # =====================================================
    # MÉTHODE PRINCIPALE – CHAT SEND (refactorisée)
    # =====================================================
    async def _handle_chat_send(self, packet: RequestPacket):
        """
        Point d'entrée pour les messages utilisateur.
        Orchestre le chargement du contexte, le routage, l'exécution des missions
        et la génération des réponses (directes ou après mission).
        """
        payload = packet.payload
        user_message = payload.get("content", "")
        forced_provider = payload.get("forced_provider", "")
        forced_model = payload.get("forced_model", "")
        session_id = payload.get("session_id", "")

        # 1. Charger / créer le contexte de session
        session_memory = await self._load_session_context(session_id)
        self.runtime_state.session_memory = session_memory

        # Enregistrer le DataProvider pour l'historique des missions de cette session
        if self.runtime_state.discovery_engine:
            mission_history_provider = MissionHistoryProvider(session_id, self.mission_store)
            self.register_data_provider("missions", mission_history_provider)
            Logger.debug(f"[Orchestrator] MissionHistoryProvider enregistré pour la session {session_id}")

        # --- GESTION DU LLM DE L'ORCHESTRATEUR ---
        if self.llm is None:
            self.llm = Llm(
                provider_manager=self.provider_manager,
                provider_id=forced_provider,
                model_id=forced_model,
                runtime_state=self.runtime_state
            )
            if self.runtime_state.discovery_engine:
                self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                Logger.info("[Orchestrator] Progressive Disclosure activée pour l'Orchestrateur (nouveau LLM).")
        else:
            # Mettre à jour le provider/model si nécessaire
            if self.llm.provider_id != forced_provider or self.llm.model_id != forced_model:
                self.llm.provider_id = forced_provider
                self.llm.model_id = forced_model
                Logger.info(f"[Orchestrator] LLM mis à jour : provider={forced_provider}, model={forced_model}")
            # Réactiver la PD pour rafraîchir l'entité et les providers
            if self.runtime_state.discovery_engine:
                try:
                    self.llm.enable_discovery(self.runtime_state.discovery_engine, self)
                    Logger.debug("[Orchestrator] Progressive Disclosure réactivée pour l'Orchestrateur (LLM existant).")
                except Exception as e:
                    Logger.error(f"[Orchestrator] Échec de la réactivation de la PD : {e}")

        # --- MISE À JOUR DE TOUS LES EXPLORATEURS AVEC LE LLM COURANT ---
        if self.runtime_state.discovery_engine:
            # Récupérer tous les explorateurs enregistrés
            for data_type, explorer in self.runtime_state.discovery_engine._explorers.items():
                if explorer.llm is not self.llm:  # Évite les assignations inutiles
                    explorer.llm = self.llm
                    Logger.debug(f"[Orchestrator] LLM assigné à l'Explorer '{data_type}'.")


        # Restaurer l'historique des signatures depuis le contexte de session
        history = session_memory.context.discovery_history or []
        if self.llm:
            self.llm.set_discovery_history(history)
            Logger.debug(f"[Orchestrator] Historique des signatures PD restauré : {len(history)} entrée(s).")
        # 2. Réarmer le système
        self.runtime_state.cancel_requested = False
        self.runtime_state.reset_execution_markers()

        if not forced_provider or not forced_model:
            raise ValueError(_("Missing forced_provider or forced_model."))

        # 3. Initialiser le Learner (une seule fois)
        await self._ensure_learner_initialized(forced_provider, forced_model)

        # 4. Préparer les contextes d'exécution
        self._prepare_execution_context(session_id, forced_provider, forced_model)

        await self.propagate_event(Events.THINKING_STARTED, {})

        try:
            # 5. Récupérer l'historique de conversation
            context_list = self.memory.get_context_for_llm(session_id) or []
            context_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context_list]) if context_list else ""

            # 6. Récupérer les conseils pour l'Orchestrateur (routage)
            advice_orchestrator = await self._get_orchestrator_advice(user_message)

            # 7. Construire le prompt d'orchestration
            loader = get_prompt_loader()
            session_context_vars = {
                "session_goal_stack": session_memory.context.goal_stack,
                "session_unresolved_issues": session_memory.context.unresolved_issues,
                "session_last_mission_status": session_memory.context.last_mission_status,
                "session_mood": session_memory.context.mood,
            }
            orchestrator_prompt = loader.load(
                "orchestrator.md",
                lang=self.runtime_state.language,
                user_message=user_message,
                history=context_str,
                advice=advice_orchestrator,
                **session_context_vars
            )

            # 8. Appeler le LLM pour la décision de routage (via le LLM de l'Orchestrateur)
            with self.runtime_state.execution_context.scope(session_id=session_id):
                decision = await self._route_orchestrator(
                    orchestrator_prompt,
                    context_list,
                    forced_provider,
                    forced_model
                )

            # Récupérer le dernier RefinedContext si une PD a eu lieu
            if hasattr(self.llm, 'get_last_refined_context'):
                refined = self.llm.get_last_refined_context()
                if refined:
                    session_memory = self.runtime_state.session_memory
                    if session_memory:
                        session_memory.context.discovery_insights = session_memory.context.discovery_insights or []
                        for entry in refined.entries:
                            session_memory.context.discovery_insights.append({
                                "question": entry.question,
                                "answer": entry.answer,
                                "tool_name": entry.tool_name,
                                "timestamp": entry.timestamp.isoformat()
                            })

                        # Synchronisation de l'historique des signatures
                        if hasattr(self.llm, '_discovery_history'):
                            session_memory.context.discovery_history = self.llm._discovery_history.copy()
                            Logger.debug(f"[Orchestrator] Historique des signatures PD synchronisé : {len(session_memory.context.discovery_history)} entrée(s).")
                        session_memory.context.touch()
                        Logger.debug(f"[Orchestrator] SessionContext enrichi avec {len(refined.entries)} insight(s) de PD.")

            # Stocker les signatures dans le contexte d'exécution
            self.current_execution_context["signatures"] = decision.signatures
            signatures = decision.signatures or [] 
            if signatures:
                Logger.info(f"[Orchestrator] Signatures extraites : {[f'{s.action} {s.object}' for s in signatures]}")
            else:
                Logger.debug("[Orchestrator] Aucune signature extraite.")
            self.runtime_state.current_signatures = signatures

            # 9. Vérifier l'annulation
            if self.runtime_state.cancel_requested:
                Logger.info("[Orchestrator] Stop demandé pendant l'évaluation, réponse ignorée.")
                await self.propagate_event(Events.THINKING_FINISHED, {})
                return ErrorPacket(type="error", message=_("Génération annulée"))

            # 10. Traiter selon le mode
            if decision.type == OrchestratorMode.DIRECT:
                return await self._handle_direct_decision(
                    decision, session_id, user_message, forced_provider
                )
            elif decision.type == OrchestratorMode.MISSION:
                return await self._handle_mission_decision(
                    decision, session_id, user_message, forced_provider, forced_model, session_memory
                )
            else:
                raise ValueError(f"Unknown OrchestratorMode: {decision.type}")

        except Exception as e:
            Logger.error(f"[Orchestrator] Critical failure during agent loop: {str(e)}")
            await self.propagate_event(Events.RUNTIME_ERROR, {"message": str(e)})
            return ErrorPacket(type="error", message=str(e))
        finally:
            await self.propagate_event(Events.THINKING_FINISHED, {})
    # =====================================================
    # SOUS‑MÉTHODES DE CHAT SEND
    # =====================================================

    async def _load_session_context(self, session_id: str) -> SessionMemory:
        """
        Charge le contexte de session depuis la base (si existant) et le restaure
        dans la mémoire RAM. Retourne l'objet SessionMemory correspondant.
        """
        session_data = self.session_store.get_session(session_id)
        if session_id not in self.session_memories:
            self.session_memories[session_id] = SessionMemory(session_id)
        session_memory = self.session_memories[session_id]

        if session_data:
            session_memory.context.goal_stack = session_data.get("goal_stack", [])
            session_memory.context.global_goal = session_data["goal_stack"][-1]["text"] if session_data.get("goal_stack") else None
            session_memory.context.mission_history = session_data.get("mission_history", [])
            session_memory.context.unresolved_issues = session_data.get("unresolved_issues", [])
            session_memory.context.mood = session_data.get("mood")
            session_memory.context.last_mission_status = session_data.get("last_mission_status")
            session_memory.context.discovery_history = session_data.get("discovery_history", [])
            Logger.info(f"[Orchestrator] Contexte restauré pour session {session_id} ({len(session_memory.context.goal_stack)} objectifs, {len(session_memory.context.mission_history)} missions)")
        else:
            Logger.debug(f"[Orchestrator] Nouvelle session : {session_id}")
        return session_memory

    async def _ensure_learner_initialized(self, forced_provider: str, forced_model: str):
        """Initialise le Learner s'il ne l'est pas déjà."""
        if not self.runtime_state.learner:
            llm_for_learner = Llm(
                provider_manager=self.provider_manager,
                provider_id=forced_provider,
                model_id=forced_model,
                runtime_state=self.runtime_state  # <--- AJOUT
            )
            self.learner = Learner(
                name="learner",
                mission_store=self.mission_store,
                runtime_state=self.runtime_state,
                llm=llm_for_learner,
                parent=self
            )
            self.runtime_state.learner = self.learner
            Logger.info(f"[Orchestrator] Learner instancié avec {forced_provider}/{forced_model}.")
            
    def _prepare_execution_context(self, session_id: str, forced_provider: str, forced_model: str):
        """Initialise les dictionnaires de contexte d'exécution pour la session."""
        self.active_sessions[session_id] = {
            "provider_id": forced_provider,
            "model_id": forced_model,
            "refined_goal": None
        }
        self.current_execution_context = {
            "session_id": session_id,
            "provider_id": forced_provider,
            "model_id": forced_model,
            "refined_goal": None
        }

    async def _get_orchestrator_advice(self, user_message: str) -> str:
        # Reranker désactivé pour l'instant.
        # advice = ""
        # if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
        #     try:
        #         advice = await self.runtime_state.learner.get_advice(
        #             entity_types=["Orchestrator"], goal=user_message
        #         )
        #     except Exception as e:
        #         Logger.error(f"[Orchestrator] Erreur récupération conseils routage : {e}")
        #         advice = ""
        # if advice:
        #     Logger.debug("[Orchestrator] Conseils reçus pour le routage.")
        # else:
        #     Logger.debug("[Orchestrator] Aucun conseil reçu pour le routage.")
        # return advice
        return ""  # <-- désactivé

    async def _route_orchestrator(self, prompt: str, context_list: list, forced_provider: str, forced_model: str) -> OrchestratorDecision:
        if self.llm is None:
            raise RuntimeError("L'Orchestrateur n'a pas de LLM. Vérifiez _handle_chat_send.")
        
        routing_started = time.monotonic()
        try:
            decision = await self.llm.generate_structured(
                prompt=prompt,
                schema=OrchestratorDecision,
                tag="OrchestratorDecision",
                mission_id=self.runtime_state.current_mission_id
            )
            # Récupérer le RefinedContext si une PD a eu lieu
            if hasattr(self.llm, 'get_last_refined_context'):
                refined = self.llm.get_last_refined_context()
                if refined:
                    Logger.debug(f"[Orchestrator] PD effectuée : {len(refined.entries)} entrées.")
            
            # SUPPRESSION du doublon : l'événement est déjà émis par _call_llm_with_schema
            # On peut garder un log de debug si besoin, mais pas un événement llm_call.
            Logger.debug(f"[Orchestrator] Décision prise en {int((time.monotonic() - routing_started)*1000)} ms.")
            return decision
        except Exception as e:
            # L'événement d'erreur est déjà émis par _call_llm_with_schema
            # On peut juste relancer l'exception après log
            Logger.error(f"[Orchestrator] Échec de la génération de la décision : {e}")
            raise
        
    async def _evaluate_learning_trigger(self, mission_context: Dict[str, Any]) -> None:
        """Évalue si l'apprentissage doit être déclenché."""
        
        if not self.runtime_state.auto_learn_enabled:
            Logger.debug("[Orchestrator] ⏭️ Apprentissage ignoré : auto_learn_enabled est False.")
            return

        # 1. Vérifier les marqueurs
        if not self.marker_manager.should_learn(mission_context):
            Logger.debug("[Orchestrator] ⏭️ Apprentissage ignoré : marqueurs insuffisants (voir logs MarkerManager).")
            return

        # 2. Vérifier l'empreinte (éviter les doublons)
        fingerprint = self.fingerprint_store.compute_fingerprint(
            goal=mission_context.get("goal"),
            plan=mission_context.get("plan", {}),
            signatures=mission_context.get("signatures", [])
        )
        if self.fingerprint_store.exists(fingerprint):
            Logger.debug("[Orchestrator] ⏭️ Apprentissage ignoré : empreinte déjà existante.")
            return

        # 3. Lancer l'analyse en arrière-plan
        Logger.info("[Orchestrator] ✅ Apprentissage déclenché ! Lancement de l'analyse en arrière-plan.")
        asyncio.create_task(self._background_learn(mission_context, fingerprint))
        
    async def _background_learn(self, mission_context: Dict[str, Any], fingerprint: str) -> None:
        """Tâche de fond pour l'analyse Learner."""
        try:
            Logger.info("[Orchestrator] 🧠 Apprentissage déclenché par les marqueurs.")
            # Sauvegarder l'empreinte avant l'analyse pour éviter les doublons
            self.fingerprint_store.save(mission_context.get("mission_id"), fingerprint)

            if not self.runtime_state.learner:
                Logger.warning("[Orchestrator] Learner non initialisé.")
                return

            analyzed = await self.runtime_state.learner.analyze_all_episodes(force=False)
            if analyzed > 0:
                Logger.info(f"[Orchestrator] ✅ {analyzed} épisode(s) analysé(s).")
            else:
                Logger.debug("[Orchestrator] Aucun nouvel épisode à analyser.")
        except asyncio.CancelledError:
            Logger.debug("[Orchestrator] Tâche d'apprentissage annulée.")
        except Exception as e:
            Logger.error(f"[Orchestrator] ❌ Erreur lors de l'apprentissage : {e}")

    async def _handle_direct_decision(
        self,
        decision: OrchestratorDecision,
        session_id: str,
        user_message: str,
        forced_provider: str
    ) -> ResponsePacket:
        """Traite une réponse directe (pas de mission)."""
        Logger.info("[Orchestrator] Requête traitée en direct answer.")
        final_response = decision.output.strip()

        self.memory.add_interaction(
            session_id=session_id,
            user_msg=user_message,
            ai_msg=final_response,
            provider_id=forced_provider
        )

        Logger.event(
            "session_turn", session_id=session_id, mode="direct",
            responder="Orchestrator", user_message=user_message, response=final_response
        )

        await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
        return ResponsePacket(type="response", status="success", payload={"message": final_response})

    
    # =====================================================
    # DANS core/orchestrator.py – remplacer la méthode _handle_mission_decision
    # =====================================================

    async def _handle_mission_decision(
        self,
        decision: OrchestratorDecision,
        session_id: str,
        user_message: str,
        forced_provider: str,
        forced_model: str,
        session_memory: SessionMemory
    ) -> ResponsePacket:
        """
        Traite une mission.
        NOUVEAU FLUX (Phase 1) :
        1. Initialisation du RUM (Registre Utile de Mission)
        2. Solver
        3. Presentator (rapport + résumé structuré) utilisant le RUM
        4. Sauvegarde complète de l'épisode (avec le résumé)
        """
        Logger.info(f"[Orchestrator] Mission identifiée. Initialisation du RootSolver.")

        # 1. Récupérer l'objectif raffiné
        refined_goal = decision.output
        self.current_execution_context["refined_goal"] = refined_goal
        self.active_sessions[session_id]["refined_goal"] = refined_goal

        # 2. Mettre à jour la mémoire de session
        session_memory.context.global_goal = refined_goal
        session_memory.context.goal_stack.append({
            "text": refined_goal,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        })
        session_memory.context.touch()

        # 3. Créer le cache de mission
        mission_id = str(uuid.uuid4())
        mission_cache = MissionCache(mission_id, session_id, refined_goal)
        mission_cache.status = "running"
        session_memory.add_mission(mission_cache)
        self.runtime_state.current_mission_id = mission_id
        self.current_execution_context["mission_cache"] = mission_cache

        Logger.event(
            "session_turn",
            session_id=session_id,
            mode="mission",
            mission_id=mission_id,
            user_message=user_message,
            refined_goal=refined_goal,
            signatures=[s.model_dump() for s in decision.signatures]
        )
        Logger.info(f"[Orchestrator] 📝 Mission cache créé : {mission_id}")

        # 4. Prévenir le frontend
        await self.propagate_event(Events.MISSION_STARTED, {"goal": refined_goal})

        # 5. Instancier le Solver racine
        self.root_solver = Solver(
            solver_id=mission_id,
            goal=refined_goal,
            parent=self,
            provider_manager=self.provider_manager,
            runtime_state=self.runtime_state,
            provider_id=forced_provider,
            model_id=forced_model,
        )

        signatures = self.current_execution_context.get("signatures", [])
        if signatures:
            self.root_solver.assign_signatures(signatures)
            Logger.info(f"[Orchestrator] Signatures assignées au root Solver : {len(signatures)}")

        # ============================================================
        # BLOC PRINCIPAL : Solver → Presentator → Sauvegarde
        # ============================================================
        result = None
        final_response = ""
        try:
            # OBSERVABILITY : pousser le mission_id
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                # --- NOUVEAU : Initialiser le RUM avant d'exécuter le Solver ---
                self.runtime_state.mission_rum = {}

                result = await self.root_solver.run()

                # ============================================================
                # GESTION DE L'ANNULATION PAR L'UTILISATEUR (après Solver)
                # ============================================================
                if self.runtime_state.cancel_requested:
                    Logger.info("[Orchestrator] Mission annulée par l'utilisateur après le Solver.")
                    mission_cache = session_memory.get_active_mission()
                    if mission_cache:
                        mission_cache.status = "cancelled"
                        mission_cache.finished_at = datetime.now()
                        if self.root_solver:
                            mission_cache.execution_tree = self.root_solver.execution_tree
                            mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
                        else:
                            mission_cache.execution_tree = {"error": "Solver not instantiated"}
                            mission_cache.resolved_data = {}

                        mission_cache.summary = f"Mission '{refined_goal}' annulée par l'utilisateur."

                        await asyncio.to_thread(
                            self.mission_store.save_episode,
                            mission_cache,
                            session_id,
                            self.runtime_state.environment
                        )
                        await self._invalidate_cache_for_mission(mission_cache.mission_id, self.root_solver.signatures if self.root_solver else [])

                        session_memory.context.last_mission_status = "cancelled"
                        session_memory.context.unresolved_issues.append(
                            f"Mission {mission_cache.mission_id} annulée par l'utilisateur."
                        )
                        await self._save_session_context(session_id, session_memory.context.to_dict())

                        await self.propagate_event(Events.MISSION_FAILED, {
                            "reason": "Mission annulée par l'utilisateur",
                            "mission_id": mission_cache.mission_id,
                            "session_id": session_id
                        })

                    await self.propagate_event(Events.THINKING_FINISHED, {})
                    return ResponsePacket(type="response", status="success",
                                        payload={"message": _("Mission annulée par l'utilisateur")})

        except asyncio.CancelledError as e:
            Logger.warning(f"[Orchestrator] Mission annulée par l'utilisateur : {e}")
            mission_cache = session_memory.get_active_mission()
            if mission_cache:
                mission_cache.status = "cancelled"
                mission_cache.finished_at = datetime.now()
                if self.root_solver:
                    mission_cache.execution_tree = self.root_solver.execution_tree
                    mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
                else:
                    mission_cache.execution_tree = {"error": "Solver not instantiated"}
                    mission_cache.resolved_data = {}
                mission_cache.summary = f"Mission '{refined_goal}' annulée par l'utilisateur."
                await asyncio.to_thread(
                    self.mission_store.save_episode,
                    mission_cache,
                    session_id,
                    self.runtime_state.environment
                )
                await self._invalidate_cache_for_mission(mission_cache.mission_id, self.root_solver.signatures if self.root_solver else [])
                session_memory.context.last_mission_status = "cancelled"
                session_memory.context.unresolved_issues.append(
                    f"Mission {mission_cache.mission_id} annulée par l'utilisateur."
                )
                await self._save_session_context(session_id, session_memory.context.to_dict())
            raise

        except Exception as e:
            Logger.error(f"[Orchestrator] Erreur critique dans le Solver : {e}")
            mission_cache = session_memory.get_active_mission()
            if mission_cache:
                mission_cache.status = "failed"
                mission_cache.finished_at = datetime.now()
                if self.root_solver:
                    mission_cache.execution_tree = self.root_solver.execution_tree
                    mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
                mission_cache.summary = f"Mission '{refined_goal}' interrompue par une erreur système : {str(e)}"
                await asyncio.to_thread(
                    self.mission_store.save_episode,
                    mission_cache,
                    session_id,
                    self.runtime_state.environment
                )
                await self._invalidate_cache_for_mission(mission_cache.mission_id, self.root_solver.signatures if self.root_solver else [])
                session_memory.context.last_mission_status = "failed"
                session_memory.context.unresolved_issues.append(
                    f"Mission {mission_cache.mission_id} interrompue par une erreur système."
                )
                await self._save_session_context(session_id, session_memory.context.to_dict())
                await self.propagate_event(Events.MISSION_FAILED, {
                    "reason": str(e),
                    "mission_id": mission_cache.mission_id,
                    "session_id": session_id
                })
            raise

        # ============================================================
        # SUITE NORMALE : le Solver a retourné un résultat
        # ============================================================

        # 6. Mettre à jour le cache mission avec les données du Solver
        mission_cache = session_memory.get_active_mission()
        if mission_cache:
            mission_cache.execution_tree = result.execution_tree
            if self.root_solver:
                mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
            mission_cache.finished_at = datetime.now()
            if result.status == ExecutionStatus.SUCCESS:
                mission_cache.status = "success"
            else:
                mission_cache.status = "failed"

            session_memory.context.mission_history.append(mission_cache.mission_id)
            session_memory.context.last_mission_status = mission_cache.status
            session_memory.context.touch()

            if mission_cache.status == "failed":
                issue = f"Mission {mission_cache.mission_id} terminée en échec"
                if result.error_reason:
                    issue += f" - {result.error_reason}"
                session_memory.context.unresolved_issues.append(issue)

        # 7. Vérifier l'annulation post-Solver
        if self.runtime_state.cancel_requested:
            Logger.info("[Orchestrator] Stop demandé après le Solver, réponse ignorée.")
            await self.propagate_event(Events.THINKING_FINISHED, {})
            return ErrorPacket(type="error", message=_("Génération annulée"))

        # 8. PRESENTATOR (rapport + résumé structuré) – UTILISE LE RUM
        try:
            presentator = Presentator(
                provider_manager=self.provider_manager,
                runtime_state=self.runtime_state,
                provider_id=forced_provider,
                model_id=forced_model
            )

            mission_status = "success" if result.status == ExecutionStatus.SUCCESS else "failed"

            # --- NOUVEAU : Utiliser le RUM comme registre pour le Presentator ---
            rum = getattr(self.runtime_state, 'mission_rum', None)
            if rum:
                Logger.debug("[Orchestrator] RUM passé au Presentator.")
            else:
                Logger.debug("[Orchestrator] Aucun RUM trouvé, utilisation du registre legacy.")
                # Fallback : utiliser le registre du root solver
                rum = self.root_solver.variable_registry if self.root_solver else {}

            # Appel unique structuré
            # Dans _handle_mission_decision, après avoir obtenu le résultat du Solver
            with self.runtime_state.execution_context.scope(mission_id=mission_id):
                output = await presentator.generate_mission_output(
                    goal=refined_goal,
                    final_context=result.final_context,
                    variable_registry=rum,
                    accumulated_response=result.response or "",
                    mission_status=mission_status,
                    error_reason=result.error_reason if mission_status == "failed" else None,
                    mission_id=mission_cache.mission_id if mission_cache else None
                )

            final_response = output.user_report
            summary = output.summary

            if mission_cache:
                mission_cache.summary = summary
                mission_cache.presentator_result = {"status": "success", "error_reason": None}

        except Exception as e:
            # CAS : ÉCHEC DU PRESENTATOR (fallback rigide)
            Logger.error(f"[Orchestrator] ⚠️ Échec du Presentator. Motif: {e}")
            if mission_cache:
                Logger.event(
                    "fallback_used",
                    mission_id=mission_cache.mission_id,
                    reason=str(e),
                    fallback_type="presentator"
                )
            if result.status == ExecutionStatus.SUCCESS:
                fallback_ctx = result.final_context[-500:] if result.final_context else "Aucun contexte disponible."
                final_response = _("Mission achevée techniquement, mais le rapport final n'a pas pu être généré.\n\n**Dernier état :**\n```\n{}\n```").format(fallback_ctx)
                summary = f"Mission '{refined_goal}' : succès technique (rapport final indisponible)."
            else:
                reason = result.error_reason or _("erreur inconnue")
                final_response = _("❌ La mission a échoué : {} (rapport final indisponible)").format(reason)
                summary = f"Mission '{refined_goal}' : échec (rapport final indisponible)."

            if mission_cache:
                mission_cache.summary = summary
                mission_cache.presentator_result = {"status": "failed", "error_reason": str(e)}

        # ============================================================
        # 9. SAUVEGARDE COMPLÈTE DE L'ÉPISODE (une seule fois, avec résumé)
        # ============================================================
        if mission_cache:
            try:
                await asyncio.to_thread(
                    self.mission_store.save_episode,
                    mission_cache,
                    session_id,
                    self.runtime_state.environment
                )
                await self._invalidate_cache_for_mission(mission_cache.mission_id, self.root_solver.signatures if self.root_solver else [])
                Logger.info(f"[Orchestrator] ✅ Épisode sauvegardé avec résumé : {mission_cache.mission_id}")
            except Exception as e:
                Logger.error(f"[Orchestrator] Échec sauvegarde épisode : {e}")

        # --- RÉCUPÉRATION DES MARQUEURS D'EXÉCUTION DEPUIS RUNTIME_STATE ---
        execution_markers = self.runtime_state.execution_markers

        # --- DÉCLENCHEMENT AUTO-LEARN (MARQUEURS) ---
        mission_context = {
            "goal": refined_goal,
            "status": mission_cache.status if mission_cache else "unknown",
            "execution_attempt": execution_markers.get("execution_attempt", 0),
            "has_abstract_task": execution_markers.get("has_abstract_task", False),
            "plan_rejected": execution_markers.get("plan_rejected", False),
            "is_novel": execution_markers.get("is_novel", False),
            "mission_id": mission_id,
            "solver_id": self.root_solver.id if self.root_solver else None,
            "session_id": session_id,
            "plan": {},
            "signatures": signatures,
        }
        await self._evaluate_learning_trigger(mission_context)

        # 10. Sauvegarde du SessionContext
        context_dict = {
            "goal_stack": session_memory.context.goal_stack,
            "unresolved_issues": session_memory.context.unresolved_issues,
            "mission_history": session_memory.context.mission_history,
            "mood": session_memory.context.mood,
            "last_mission_status": session_memory.context.last_mission_status,
            "discovery_history": session_memory.context.discovery_history,  # <-- AJOUT
        }
        await self._save_session_context(session_id, context_dict)

        # Thèmes récurrents (optionnel)
        themes = self.session_store.get_recurrent_themes(session_id, limit=5)
        if themes:
            session_memory.context.recurrent_themes = themes

        # 11. Enregistrer l'interaction dans l'historique
        self.memory.add_interaction(
            session_id=session_id,
            user_msg=user_message,
            ai_msg=final_response,
            provider_id=forced_provider
        )
        Logger.info(f"[Orchestrator] ✅ Interaction consolidée pour la session {session_id}")

        # 12. Émettre la réponse finale
        await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
        return ResponsePacket(type="response", status="success", payload={"message": final_response})

    def _has_abstract_task_in_plan(self, plan) -> bool:
        if not plan or not hasattr(plan, 'steps'):
            return False
        return any(step.type.value == "abstract_task" for step in plan.steps)

    def _was_plan_rejected(self) -> bool:
        # On pourrait lire un flag dans runtime_state ou vérifier les logs
        # Pour l'instant, on suppose que False par défaut
        return False     
    # =====================================================
    # MÉTHODE DE SAUVEGARDE DU SESSION CONTEXT
    # =====================================================
    async def _save_session_context(self, session_id: str, context_dict: dict):
        """Sauvegarde asynchrone du contexte de session en base."""
        try:
            await asyncio.to_thread(self.session_store.upsert_session, session_id, context_dict)
        except Exception as e:
            Logger.error(f"[Orchestrator] Erreur sauvegarde session {session_id} : {e}")

    # =====================================================
    # IMPLÉMENTATION DE SUPERVISOR
    # =====================================================
    async def validate_plan(self, plan: Plan, child_solver_id: str) -> bool:
        """Valide un plan (actuellement toujours accepté)."""
        Logger.info(f"[Orchestrator] ⚖️ Validation du plan du Solver '{child_solver_id}'")
        target_goal = self.current_execution_context.get("refined_goal") or plan.goal
        Logger.info(f"[Orchestrator] [CONVERGENCE CHECK] Objectif cible : '{target_goal}'")
        for step in plan.steps:
            Logger.info(f"   -> Étape : {step.description} [{step.type.value}]")
        # Ici sera ajouté le LLM Judge plus tard
        Logger.info("[Orchestrator] ✅ Le plan converge vers l'objectif raffiné. Feu vert.")
        return True

    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        Logger.error(f"[Orchestrator] 🚨 ALERTE CRITIQUE du Solver '{child_solver_id}' : {error_context}")
        await self.propagate_event(Events.RUNTIME_ERROR, {
            "message": _("Alerte critique de la branche {}: {}").format(child_solver_id, error_context)
        })

    # =====================================================
    # GESTION DES OUTILS
    # =====================================================
    async def _handle_tool_result(self, packet: RequestPacket):
        """Réception du résultat d'un outil depuis le frontend."""
        payload = packet.payload
        call_id = payload.get("call_id")
        tool_result = payload.get("result", "")
        Logger.info(f"[Orchestrator] 📥 Retour matériel reçu pour l'ID: {call_id}")

        if call_id in self.pending_tool_calls:
            self.pending_tool_calls[call_id].set_result(tool_result)
            return ResponsePacket(type="response", status="success", payload={"message": _("Result routed to solver.")})
        else:
            Logger.error(f"[Orchestrator] Aucun solver en attente pour l'ID: {call_id}")
            return ErrorPacket(type="error", message=_("No pending context found for call_id: {}").format(call_id))

    async def _handle_chat_stop(self):
        Logger.info("[Orchestrator] 🛑 ARRET D'URGENCE DEMANDE PAR L'UI")
        self.runtime_state.cancel_requested = True
        for call_id, future in self.pending_tool_calls.items():
            if not future.done():
                future.set_result(_('{"result": false, "message": "Exécution interrompue par l\'utilisateur."}'))
        self.pending_tool_calls.clear()
        return ResponsePacket(type="response", status="success", payload={"message": _("Stop signal broadcasted")})

    # Dans core/orchestrator.py, méthode execute_tool

    async def execute_tool(self, tool_name: str, arguments: dict, llm: Optional[Llm] = None) -> str:
        """
        Point d'entrée pour l'exécution d'un outil.
        Délègue au ToolsManager, qui gère la logique interne/externe.
        """
        return await self.runtime_state.tools_manager.execute_tool(tool_name, arguments, llm=llm)

    # Dans core/orchestrator.py

    async def _execute_external_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Méthode privée appelée par ToolsManager pour exécuter un outil externe (C++).
        """
        is_valid = self.runtime_state.tools_manager.validate_tool_call(tool_name, arguments)
        if not is_valid:
            return json.dumps({"result": False, "data": None, "message": "Tool not found"})

        call_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_tool_calls[call_id] = future

        Logger.info(f"[Orchestrator] 📤 Dispatch outil [{tool_name}] (ID: {call_id})")
        await self.propagate_event(Events.TOOL_REQUESTED, {
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": arguments
        })

        try:
            result = await future
            return result
        finally:
            self.pending_tool_calls.pop(call_id, None)

    # =====================================================
    # PROPAGATION D'ÉVÉNEMENTS ET HEARTBEAT
    # =====================================================
    async def propagate_event(self, event_name: str, payload: dict):
        """Propagation des événements vers le C++ (via event_bus)."""
        if event_name == Events.THINKING_STARTED:
            if self._heartbeat_task is None or self._heartbeat_task.done():
                self._heartbeat_task = asyncio.create_task(self._send_heartbeat())
                Logger.debug("[Orchestrator] Heartbeat started.")
        elif event_name in (Events.THINKING_FINISHED, Events.RUNTIME_ERROR, Events.MISSION_FAILED):
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None
                Logger.debug("[Orchestrator] Heartbeat stopped.")

        if "session_id" not in payload and self.current_execution_context.get("session_id"):
            payload["session_id"] = self.current_execution_context["session_id"]

        await self.event_bus.emit(event_name, payload)

    async def _send_heartbeat(self):
        """Envoie un événement HEARTBEAT toutes les 30 secondes."""
        try:
            while not self.runtime_state.cancel_requested:
                await asyncio.sleep(30)
                await self.propagate_event(Events.HEARTBEAT, {})
        except asyncio.CancelledError:
            Logger.debug("[Orchestrator] Heartbeat task cancelled.")
            raise

    async def _invalidate_cache_for_mission(self, mission_id: str, signatures: List) -> None:
        """Invalide le cache pour les signatures d'une mission."""
        if not signatures:
            return
        try:
            from core.cache import CacheManager
            normalized_markers = CacheManager()._normalize_signatures(
                [{"action": s.action, "object": s.object} for s in signatures]
            )
            if normalized_markers:
                cache_mgr = self.runtime_state.cache_manager or CacheManager()
                invalidated = await cache_mgr.invalidate(normalized_markers)
                if invalidated > 0:
                    Logger.debug(f"[Orchestrator] Cache invalidé pour {invalidated} entrée(s) suite à la mission {mission_id}")
        except Exception as e:
            Logger.warning(f"[Orchestrator] Échec de l'invalidation du cache : {e}")

    # =====================================================
    # CONFIGURATION RUNTIME
    # =====================================================
    async def _handle_runtime_configure(self, packet: RequestPacket):
        Logger.info("Runtime configuration started")
        payload = packet.payload
        self.runtime_state.system_prompt = payload.get("system_prompt", "")
        self.runtime_state.language = payload.get("language", "en")
        self.runtime_state.environment = payload.get("environment", "simulated")
        Logger.info(f"[Orchestrator] Environnement = {self.runtime_state.environment}")
        self.runtime_state.presentator_detail_level = payload.get("presentator_detail_level",
                                                                  "brief")

        from core.i18n import setup_i18n
        setup_i18n(self.runtime_state.language)

        # Dans _handle_runtime_configure, remplacer le bloc concernant ToolsManager par :

        raw_tools = payload.get("tools", [])

        # Créer le ToolsManager en tant qu'entité (sans LLM dédié)
        tools_manager = ToolsManager(
            name="tools_manager",
            llm=None,  # Pas de LLM par défaut
            parent=self,
            runtime_state=self.runtime_state
        )
        self.runtime_state.tools_manager = tools_manager

        # Charger les outils externes depuis le payload
        if raw_tools:
            self.runtime_state.tools_manager.load_tools_from_payload(raw_tools)
            Logger.info(f"[Orchestrator] {len(raw_tools)} outils externes chargés.")

        # Référence à l'Orchestrateur pour les appels d'outils externes (C++)
        self.runtime_state.orchestrator = self
        # =====================================================
        # INITIALISATION DES EMBEDDING PROVIDERS
        # =====================================================
        embedding_models = payload.get("embedding_models", [])
        if embedding_models:
            self.runtime_state.embedding_manager.set_emitter(self.propagate_event)

            from embeddings.providers.sentence_transformer import SentenceTransformerProvider

            for model_def in embedding_models:
                model_id = model_def.get("id")
                if not model_id:
                    continue
                prefix_query = model_def.get("prefix_query", "")
                prefix_passage = model_def.get("prefix_passage", "")
                display_name = model_def.get("display_name", model_id)

                provider = SentenceTransformerProvider(
                    model_id=model_id,
                    display_name=display_name,
                    prefix_query=prefix_query,
                    prefix_passage=prefix_passage,
                    emit_func=self.propagate_event
                )
                self.runtime_state.embedding_manager.register_provider(provider)

            active_model = payload.get("active_embedding_model")
            if active_model and active_model in self.runtime_state.embedding_manager._providers:
                self.runtime_state.embedding_manager.set_active_provider(active_model)
            else:
                providers = self.runtime_state.embedding_manager.list_providers()
                if providers:
                    first_id = providers[0]["id"]
                    self.runtime_state.embedding_manager.set_active_provider(first_id)
                    Logger.info(f"[Orchestrator] Fallback : modèle d'embedding actif = {first_id}")

            Logger.info(f"[Orchestrator] {len(embedding_models)} modèle(s) d'embedding enregistré(s).")
        else:
            # Fallback : modèle par défaut
            Logger.warning("[Orchestrator] Aucun embedding_models dans le payload. Utilisation du modèle par défaut.")
            from embeddings.providers.sentence_transformer import SentenceTransformerProvider
            default_provider = SentenceTransformerProvider(
                model_id="sentence-transformers/all-MiniLM-L6-v2",
                display_name="MiniLM L6 (anglais)",
                emit_func=self.propagate_event
            )
            self.runtime_state.embedding_manager.register_provider(default_provider)
            self.runtime_state.embedding_manager.set_active_provider("sentence-transformers/all-MiniLM-L6-v2")

        api_keys = payload.get("api_keys", {})
        models_registry = payload.get("models_registry", {})

        self.provider_manager.clear()
        validated_models = []

        registry_providers = models_registry.get("providers", {})
        for provider_key, provider_data in registry_providers.items():
            normalized_key = provider_key.lower()
            if normalized_key not in api_keys or not api_keys[normalized_key]:
                continue
            api_key = api_keys[normalized_key]
            for model in provider_data.get("models", []):
                enriched_model = dict(model)
                enriched_model["provider_id"] = normalized_key
                if "display_name" not in enriched_model:
                    enriched_model["display_name"] = enriched_model["id"]
                validated_models.append(enriched_model)

            if normalized_key == Providers.GEMINI:
                p = GeminiProvider(api_key, "default", self.runtime_state.system_prompt)
                p.provider_id = Providers.GEMINI
                self.provider_manager.register_provider(p)
            elif normalized_key == Providers.GROQ:
                p = GroqProvider(api_key, "default", self.runtime_state.system_prompt)
                p.provider_id = Providers.GROQ
                self.provider_manager.register_provider(p)
            elif normalized_key == Providers.OPENAI:
                p = OpenAIProvider(api_key, "default", self.runtime_state.system_prompt)
                p.provider_id = Providers.OPENAI
                self.provider_manager.register_provider(p)
            elif normalized_key == Providers.OPENROUTER:
                p = OpenRouterProvider(api_key, "default", self.runtime_state.system_prompt)
                p.provider_id = Providers.OPENROUTER
                self.provider_manager.register_provider(p)

        await self.provider_manager.initialize()
        self.runtime_state.is_configured = True
        Logger.set_runtime_state(self.runtime_state)

        # --- Configuration du cache ---
        cache_max_entries = payload.get("cache_max_entries", 1000)
        cache_ttl_seconds = payload.get("cache_ttl_seconds", 7 * 24 * 3600)

        from core.cache import CacheManager
        cache_mgr = CacheManager()
        cache_mgr.set_max_entries(cache_max_entries)
        cache_mgr.set_ttl(cache_ttl_seconds)

        # On stocke l'instance dans runtime_state pour que les autres composants puissent l'utiliser
        self.runtime_state.cache_manager = cache_mgr
        Logger.info(f"[Orchestrator] Cache configuré : max_entries={cache_max_entries}, ttl={cache_ttl_seconds}s")

        from core.discovery import DiscoveryEngine, RegistryExplorer
        if not self.runtime_state.discovery_engine:
            self.runtime_state.discovery_engine = DiscoveryEngine(self.runtime_state)
            registry_explorer = RegistryExplorer(self.runtime_state)
            self.runtime_state.discovery_engine.register_explorer(registry_explorer)
            Logger.info("[Orchestrator] DiscoveryEngine initialisé avec RegistryExplorer.")
        else:
            Logger.debug("[Orchestrator] DiscoveryEngine déjà existant, réutilisation.")

        # --- TOUJOURS enregistrer le MissionHistoryExplorer ---
        if self.runtime_state.discovery_engine:
            from core.discovery.explorers.mission_history_explorer import MissionHistoryExplorer
            missions_explorer = MissionHistoryExplorer(
                runtime_state=self.runtime_state,
                entity=self
            )
            self.runtime_state.discovery_engine.register_explorer(missions_explorer)
            Logger.info("[Orchestrator] MissionHistoryExplorer enregistré.")
        else:
            Logger.warning("[Orchestrator] DiscoveryEngine non disponible, impossible d'enregistrer MissionHistoryExplorer.")        
        await self.propagate_event(Events.RUNTIME_CONFIGURED, {"available_models": validated_models})
        return ResponsePacket(type="response", status="success", payload={"models_count": len(validated_models)})
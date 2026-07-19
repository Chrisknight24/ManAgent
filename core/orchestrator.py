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
from typing import Optional, Dict, List, Tuple

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
        self.runtime_state.session_memory = session_memory   # <-- partout

        # 2. Réarmer le système
        self.runtime_state.cancel_requested = False

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
            
            # Avant l'appel à loader.load("orchestrator.md", ...)
            session_context_vars = {
                "session_goal_stack": session_memory.context.goal_stack,
                "session_unresolved_issues": session_memory.context.unresolved_issues,
                "session_last_mission_status": session_memory.context.last_mission_status,
                "session_mood": session_memory.context.mood,  # pour plus tard
            }
            orchestrator_prompt = loader.load(
                "orchestrator.md",
                lang=self.runtime_state.language,
                user_message=user_message,
                history=context_str,
                advice=advice_orchestrator,
                **session_context_vars  # <-- injection
            )

            # 8. Appeler le LLM pour la décision de routage
            decision = await self._route_orchestrator(
                orchestrator_prompt,
                context_list,
                forced_provider,
                forced_model
            )

            # Stocker les signatures dans le contexte d'exécution pour les utiliser plus tard
            self.current_execution_context["signatures"] = decision.signatures

            # Récupérer les signatures (peut être vide)
            signatures = decision.signatures or [] 
            if signatures:
                Logger.info(f"[Orchestrator] Signatures extraites : {[f'{s.action} {s.object}' for s in signatures]}")
            else:
                Logger.debug("[Orchestrator] Aucune signature extraite.")

            self.runtime_state.current_signatures = signatures  # <-- Ajout

            # Ensuite, dans le cas MISSION, on peut passer ces signatures au Solver
            # (par exemple en les ajoutant à un contexte ou en les stockant dans l'exécution)
            # Pour le moment, on les logge simplement.

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
                model_id=forced_model
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
        """Récupère les conseils pour l'Orchestrateur (routage) depuis le Learner."""
        advice = ""
        if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
            try:
                advice = await self.runtime_state.learner.get_advice(
                    entity_types=["Orchestrator"], goal=user_message
                )
            except Exception as e:
                Logger.error(f"[Orchestrator] Erreur récupération conseils routage : {e}")
                advice = ""
            if advice:
                Logger.debug("[Orchestrator] Conseils reçus pour le routage.")
            else:
                Logger.debug("[Orchestrator] Aucun conseil reçu pour le routage.")
        return advice

    async def _route_orchestrator(
        self,
        prompt: str,
        context_list: list,
        forced_provider: str,
        forced_model: str
    ) -> OrchestratorDecision:
        """
        Appelle le ProviderManager pour obtenir une décision structurée (direct/mission).
        Instrumente l'appel pour l'observabilité.
        """
        routing_started = time.monotonic()
        try:
            decision = await self.provider_manager.generate_structured_output(
                prompt=prompt,
                provider_id=forced_provider,
                model_id=forced_model,
                response_schema=OrchestratorDecision,
                context=context_list
            )
            Logger.event(
                "llm_call", tag="OrchestratorDecision", kind="structured",
                schema="OrchestratorDecision", provider_id=forced_provider, model_id=forced_model,
                prompt=prompt, context=context_list,
                response=decision.model_dump(mode='json'),
                duration_ms=int((time.monotonic() - routing_started) * 1000), success=True
            )
            return decision
        except Exception as e:
            Logger.event(
                "llm_call", tag="OrchestratorDecision", kind="structured",
                schema="OrchestratorDecision", provider_id=forced_provider, model_id=forced_model,
                prompt=prompt, context=context_list,
                error=str(e), error_type=type(e).__name__,
                duration_ms=int((time.monotonic() - routing_started) * 1000), success=False
            )
            raise

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
        Traite une mission. Sauvegarde l'épisode (même en échec) puis relève l'exception
        pour que le front reçoive le message technique réel.
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

        # Stocker le cache dans le contexte pour le récupérer en cas d'exception
        self.current_execution_context["mission_cache"] = mission_cache

        Logger.event(
            "session_turn", session_id=session_id, mode="mission",
            mission_id=mission_id, user_message=user_message, refined_goal=refined_goal
        )
        Logger.info(f"[Orchestrator] 📝 Mission cache créé : {mission_id} pour la session {session_id}")

        # 4. Prévenir le frontend
        await self.propagate_event(Events.MISSION_STARTED, {"goal": refined_goal})

        # 5. Instancier le Solver racine
        self.root_solver = Solver(
            solver_id=mission_id,   # <-- UUID au lieu de "root"
            goal=refined_goal,
            parent=self,
            provider_manager=self.provider_manager,
            runtime_state=self.runtime_state,
            provider_id=forced_provider,
            model_id=forced_model,
        )

        # Récupérer les signatures (déjà extraites par l'Orchestrateur)
        signatures = self.current_execution_context.get("signatures", [])
        if signatures:
            self.root_solver.assign_signatures(signatures)
            Logger.info(f"[Orchestrator] Signatures assignées au root Solver : {len(signatures)}")

        # ============================================================
        # BLOC PROTÉGÉ : exécution du Solver, sauvegarde, Presentator
        # ============================================================
        result = None
        final_response = ""
        try:
            # 6. Exécuter le Solver
            result = await self.root_solver.run()
        except Exception as e:
            # ---------------------------------------------------------
            # CAS 1 : ERREUR CRITIQUE DANS LE SOLVER (API, timeout, etc.)
            # ---------------------------------------------------------
            Logger.error(f"[Orchestrator] Erreur critique dans le Solver : {e}")

            # Récupérer le cache mission (doit exister)
            mission_cache = session_memory.get_active_mission()
            if mission_cache:
                # Marquer comme failed
                mission_cache.status = "failed"
                mission_cache.finished_at = datetime.now()

                # Récupérer l'arbre partiel si le solver a été instancié
                if self.root_solver:
                    mission_cache.execution_tree = self.root_solver.execution_tree
                    mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
                else:
                    # Le solver n'a même pas été créé (cas très rare)
                    mission_cache.execution_tree = {"error": "Solver not instantiated"}
                    mission_cache.resolved_data = {}

                # Sauvegarder l'épisode en base (même en échec)
                try:
                    await asyncio.to_thread(
                        self.mission_store.save_episode,
                        mission_cache,
                        self.current_execution_context.get("session_id"),
                        self.runtime_state.environment
                    )
                    Logger.info(f"[Orchestrator] Épisode d'échec sauvegardé pour {mission_cache.mission_id}")
                except Exception as save_err:
                    Logger.error(f"[Orchestrator] Échec de la sauvegarde de l'épisode d'échec : {save_err}")

                # Mettre à jour le contexte de session
                session_memory.context.last_mission_status = "failed"
                session_memory.context.unresolved_issues.append(
                    f"Mission {mission_cache.mission_id} interrompue par une erreur système."
                )
                context_dict = {
                    "goal_stack": session_memory.context.goal_stack,
                    "unresolved_issues": session_memory.context.unresolved_issues,
                    "mission_history": session_memory.context.mission_history,
                    "mood": session_memory.context.mood,
                    "last_mission_status": session_memory.context.last_mission_status
                }
                await self._save_session_context(
                    self.current_execution_context.get("session_id"), context_dict
                )

            # ---> RELANCER L'EXCEPTION pour que le front reçoive le message technique
            raise   # lève la même exception

        # ============================================================
        # SUITE NORMALE : le Solver a retourné un résultat (succès ou échec fonctionnel)
        # ============================================================

        # 7. Sauvegarder l'arbre d'exécution (via ExecutionSerializer) - même en échec
        from core.execution_serializer import ExecutionSerializer
        sid = self.current_execution_context.get("session_id")
        pid = self.current_execution_context.get("provider_id")
        mid = self.current_execution_context.get("model_id")

        ExecutionSerializer.save_mission(
            mission_id=self.root_solver.id,
            goal=refined_goal,
            execution_tree=result.execution_tree,
            resolved_data=self.root_solver.variable_registry,
            status=result.status.value,
            final_response=result.response if result.status == ExecutionStatus.SUCCESS else None,
            final_context=result.final_context,
            session_id=sid,
            provider_id=pid,
            model_id=mid,
            parent_step_id=self.root_solver.parent_step_id,
            depth=self.root_solver.depth
        )

        # 8. Mettre à jour le cache mission
        mission_cache = session_memory.get_active_mission()
        if mission_cache:
            mission_cache.execution_tree = result.execution_tree
            if self.root_solver and hasattr(self.root_solver, 'variable_registry'):
                mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
            mission_cache.finished_at = datetime.now()
            if result.status == ExecutionStatus.SUCCESS:
                mission_cache.status = "success"
            elif self.runtime_state.cancel_requested:
                mission_cache.status = "cancelled"
            else:
                mission_cache.status = "failed"

            session_memory.context.mission_history.append(mission_cache.mission_id)
            session_memory.context.last_mission_status = mission_cache.status
            session_memory.context.touch()

            if mission_cache.status in ("failed", "cancelled"):
                issue = f"Mission {mission_cache.mission_id} terminée en {mission_cache.status}"
                if result.error_reason:
                    issue += f" - {result.error_reason}"
                session_memory.context.unresolved_issues.append(issue)

            Logger.info(f"[Orchestrator] ✅ Mission cache mis à jour : {mission_cache.mission_id} (status={mission_cache.status})")

            # Sauvegarde de l'épisode en base (obligatoire même en échec)
            try:
                await asyncio.to_thread(
                    self.mission_store.save_episode,
                    mission_cache,
                    session_id,
                    self.runtime_state.environment
                )
            except Exception as e:
                Logger.error(f"[Orchestrator] Échec sauvegarde base : {e}")

            # --- Stockage des signatures comme MissionProfiles (seulement si succès) ---
            if result.status == ExecutionStatus.SUCCESS:
                signatures = self.current_execution_context.get("signatures", [])
                if signatures:
                    embedder = get_embedding_service()
                    store = MissionProfileStore()
                    for idx, sig in enumerate(signatures):
                        signature_text = f"{sig.action} {sig.object}"
                        embedding = await embedder.embed(signature_text)
                        store.insert_profile(
                            mission_id=mission_cache.mission_id,
                            signature_text=signature_text,
                            embedding=embedding,
                            action=sig.action,
                            object=sig.object,
                            desired_state=sig.desired_state,
                            signature_index=idx,
                            signature_count=len(signatures),
                            embedding_model=embedder.model_name,
                            embedding_dimension=await embedder.dimension
                        )
                    Logger.info(f"[Orchestrator] ✅ {len(signatures)} embedding(s) stocké(s) pour la mission {mission_cache.mission_id}")

            # Sauvegarde du contexte de session (toujours)
            context_dict = {
                "goal_stack": session_memory.context.goal_stack,
                "unresolved_issues": session_memory.context.unresolved_issues,
                "mission_history": session_memory.context.mission_history,
                "mood": session_memory.context.mood,
                "last_mission_status": session_memory.context.last_mission_status
            }
            asyncio.create_task(self._save_session_context(session_id, context_dict))

            # Thèmes récurrents (si tu veux les garder)
            themes = self.session_store.get_recurrent_themes(session_id, limit=5)
            if themes:
                session_memory.context.recurrent_themes = themes
                Logger.info(f"[Orchestrator] Thèmes récurrents : {themes}")

        # 9. Vérifier l'annulation
        if self.runtime_state.cancel_requested:
            Logger.info("[Orchestrator] Stop demandé pendant le RootSolver, réponse finale ignorée.")
            await self.propagate_event(Events.THINKING_FINISHED, {})
            return ErrorPacket(type="error", message=_("Génération annulée"))

        # ============================================================
        # 10. GÉNÉRATION DU RAPPORT FINAL (Presentator) – protégé
        # ============================================================
        try:
            if result.status == ExecutionStatus.SUCCESS:
                presentator = Presentator(
                    provider_manager=self.provider_manager,
                    runtime_state=self.runtime_state,
                    provider_id=forced_provider,
                    model_id=forced_model
                )
                final_response = await presentator.generate_mission_report(
                    goal=refined_goal,
                    final_context=result.final_context,
                    variable_registry=self.root_solver.variable_registry,
                    accumulated_response=result.response
                )
                if mission_cache:
                    mission_cache.presentator_result = {"status": "success", "error_reason": None}
                    await asyncio.to_thread(
                        self.mission_store.update_presentator_result,
                        mission_cache.mission_id, mission_cache.presentator_result
                    )
            else:
                # Échec fonctionnel (le solver a retourné un échec sans exception)
                Logger.warning(f"[Orchestrator] ⚠️ Résolution avortée. Raison : {result.error_reason}")
                await self.propagate_event(Events.MISSION_FAILED, {"reason": result.error_reason})

                presentator = Presentator(
                    provider_manager=self.provider_manager,
                    runtime_state=self.runtime_state,
                    provider_id=forced_provider,
                    model_id=forced_model
                )
                final_response = await presentator.generate_error_report(
                    goal=refined_goal,
                    error_reason=result.error_reason,
                    final_context=result.final_context
                )
                if mission_cache:
                    mission_cache.presentator_result = {"status": "success", "error_reason": None}
                    await asyncio.to_thread(
                        self.mission_store.update_presentator_result,
                        mission_cache.mission_id, mission_cache.presentator_result
                    )

        except Exception as e:
            # Si le Presentator lui-même plante, on le catch et on produit un fallback
            Logger.error(f"[Orchestrator] ⚠️ Échec du Presentator. Motif: {e}")
            if mission_cache:
                mission_cache.presentator_result = {"status": "failed", "error_reason": str(e)}
                await asyncio.to_thread(
                    self.mission_store.update_presentator_result,
                    mission_cache.mission_id, mission_cache.presentator_result
                )
            if result.status == ExecutionStatus.SUCCESS:
                fallback_ctx = result.final_context[-500:] if result.final_context else "Aucun contexte disponible."
                final_response = _("Mission achevée techniquement, mais le rapport final n'a pas pu être généré.\n\n**Dernier état :**\n```\n{}\n```").format(fallback_ctx)
            else:
                final_response = _("❌ La mission a échoué : {} (rapport final indisponible)").format(result.error_reason or "erreur inconnue")

        # 11. Enregistrer l'interaction dans l'historique
        self.memory.add_interaction(
            session_id=session_id,
            user_msg=user_message,
            ai_msg=final_response,
            provider_id=forced_provider
        )
        Logger.info(f"[Orchestrator] ✅ Interaction consolidée en mémoire pour la session {session_id}")

        # 12. Émettre la réponse finale
        await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
        return ResponsePacket(type="response", status="success", payload={"message": final_response})
        
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

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Demande d'exécution d'un outil (dispatch vers le frontend)."""
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

        from tools.tools_manager import ToolsManager
        self.runtime_state.tools_manager = ToolsManager()
        raw_tools = payload.get("tools", [])
        self.runtime_state.tools_manager.load_tools_from_payload(raw_tools)

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

        await self.propagate_event(Events.RUNTIME_CONFIGURED, {"available_models": validated_models})
        return ResponsePacket(type="response", status="success", payload={"models_count": len(validated_models)})
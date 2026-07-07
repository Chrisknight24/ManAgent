"""
orchestrator.py
===============
Orchestrateur central du runtime (Architecture Hub & Spoke).
RÔLE (Le PDG) : Centre de validation, de routage des paquets C++ et Superviseur Suprême.
"""

import asyncio
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
from typing import Optional, Dict,List, Tuple

from core.prompt_loader import get_prompt_loader
from core.i18n import _

import uuid
import copy
from datetime import datetime
from memory.session_memory import SessionMemory, MissionCache
from memory.mission_store import MissionStore
from core.learner import Learner

import json
class Orchestrator(Supervisor, Entity):
    # Limite de sécurité pour la récursion des Solvers
    

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
        self.session_memories: Dict[str, SessionMemory] = {}  # <--- NOUVEAU
        self.mission_store = MissionStore()  # <--- NOUVEAU
        
    async def process(self, packet: RequestPacket):
        """
        Implémentation de la méthode abstraite de Entity.
        Point d'entrée générique pour toute entité.
        """
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
                    # --- PHASE 1 : nettoyage de la mémoire session ---
                    if session_id in self.session_memories:
                        del self.session_memories[session_id]
                        Logger.info(f"[Orchestrator] SessionMemory supprimée pour {session_id}")
                return ResponsePacket(type="response", status="success",
                                    payload={"message": _("Session purged")})
            else:
                return ErrorPacket(type="error", message=_("Unknown action: {}").format(packet.action)) # <-- Changement ici
        except Exception as e:
            Logger.error(f"Orchestrator critical error: {str(e)}") # Log reste en f""
            await self.propagate_event(Events.RUNTIME_ERROR, {"message": str(e)})
            return ErrorPacket(type="error", message=str(e))
    
    async def _handle_chat_send(self, packet: RequestPacket):
        payload = packet.payload
        user_message = payload.get("content", "")
        forced_provider = payload.get("forced_provider", "")
        forced_model = payload.get("forced_model", "")
        session_id = payload.get("session_id", "")

        # 1. On réarme le système pour la nouvelle requête
        self.runtime_state.cancel_requested = False

        if not forced_provider or not forced_model:
            raise ValueError(_("Missing forced_provider or forced_model."))

        # --- PHASE 3 : Initialisation du Learner (une seule fois) ---
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
        # Préparation initiale des contextes d'exécution
        self.active_sessions[session_id] = {
            "provider_id": forced_provider, 
            "model_id": forced_model,
            "refined_goal": None  # Sera hydraté après l'appel LLM
        }
        self.current_execution_context = {
            "session_id": session_id, 
            "provider_id": forced_provider,
            "model_id": forced_model,
            "refined_goal": None
        }

        await self.propagate_event(Events.THINKING_STARTED, {})

        try:
            # 2. Extraction sécurisée de l'historique
            context_list = self.memory.get_context_for_llm(session_id) or []
            context_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context_list]) if context_list else ""

            # Formatage du prompt d'orchestration combinant les consignes système et l'entrée utilisateur
            #orchestrator_prompt = f"{SysPrompt.ORCHESTRATOR_ROUTING}\n\nUtilisateur: {user_message}"
            
            # --- Conseil pour l'Orchestrateur (routage) : pas encore de "goal" de mission à ce
            # stade (la mission n'est identifiée qu'après ce routage) — on utilise user_message
            # comme meilleur proxy disponible pour juger la pertinence des leçons de routage.
            advice_orchestrator = ""
            if hasattr(self.runtime_state, 'learner') and self.runtime_state.learner:
                try:
                    advice_orchestrator = await self.runtime_state.learner.get_advice(
                        entity_types=["Orchestrator"], goal=user_message
                    )
                except Exception as e:
                    Logger.error(f"[Orchestrator] Erreur récupération conseils routage : {e}")
                    advice_orchestrator = ""
                if advice_orchestrator:
                    Logger.debug("[Orchestrator] Conseils reçus pour le routage.")
                else:
                    Logger.debug("[Orchestrator] Aucun conseil reçu pour le routage.")

            loader = get_prompt_loader()
            orchestrator_prompt = loader.load(
                "orchestrator.md",
                lang=self.runtime_state.language,
                user_message=user_message,
                history=context_str,
                advice=advice_orchestrator  # <--- injection
            )

            Logger.info(f"[Orchestrator] Évaluation de la requête via ProviderManager ({forced_provider}/{forced_model})...")

            # 3. Appel UNIQUE pour obtenir la décision structurée (routage + réponse/contexte)
            decision: OrchestratorDecision = await self.provider_manager.generate_structured_output(
                prompt=orchestrator_prompt,
                provider_id=forced_provider,
                model_id=forced_model,
                response_schema=OrchestratorDecision,
                context=context_list
            )

            # SÉCURITÉ DE SORTIE : Validation immédiate après retour de l'évaluation
            if self.runtime_state.cancel_requested:
                Logger.info("[Orchestrator] Stop demandé pendant l'évaluation, réponse ignorée.")
                await self.propagate_event(Events.THINKING_FINISHED, {})
                return ErrorPacket(type="error", message= _("Génération annulée"))

            # ---------------------------------------------------------
            # BRANCHE A : RÉPONSE DIRECTE (Bypass complet du RootSolver)
            # ---------------------------------------------------------
            if decision.type == OrchestratorMode.DIRECT:
                Logger.info("[Orchestrator] Requête traitée en direct answer.")
                final_response = decision.output.strip()

                # Enregistrement immédiat dans l'historique de session
                self.memory.add_interaction(
                    session_id=session_id,
                    user_msg=user_message,
                    ai_msg=final_response,
                    provider_id=forced_provider
                )

                await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
                return ResponsePacket(type="response", status="success", payload={"message": final_response})

            # ---------------------------------------------------------
            # BRANCHE B : MISSION COMPLEXE (Instanciation de l'arbre HTN)
            # ---------------------------------------------------------
            elif decision.type == OrchestratorMode.MISSION:
                Logger.info(f"[Orchestrator] Mission identifiée. Initialisation du RootSolver.")
                
                # TRUC PLUS PRO : On capture et on verrouille l'objectif raffiné par l'Orchestrateur
                refined_goal = decision.output 
                self.current_execution_context["refined_goal"] = refined_goal
                self.active_sessions[session_id]["refined_goal"] = refined_goal


                # --- PHASE 1 : INITIALISATION DE LA MÉMOIRE DE SESSION ---
                if session_id not in self.session_memories:
                    self.session_memories[session_id] = SessionMemory(session_id)
                session_memory = self.session_memories[session_id]

                # Mise à jour du contexte global
                session_memory.context.global_goal = refined_goal
                session_memory.context.touch()

                # Création du cache de mission
                mission_id = str(uuid.uuid4())
                mission_cache = MissionCache(mission_id, session_id, refined_goal)
                mission_cache.status = "running"
                session_memory.add_mission(mission_cache)

                Logger.info(f"[Orchestrator] 📝 Mission cache créé : {mission_id} pour la session {session_id}")

                # --- 1.2 : L'appel à prepare_advice() a été retiré ici. Son résultat était stocké
                # dans Advisor.advice_cache["Planner"], mais plus rien ne le lit depuis que
                # Planner.propose_plan() fait son propre appel get_advice() à la demande (avec le
                # goal exact de CHAQUE Solver, racine ou enfant — plus précis que le goal racine
                # seul). Cet appel ne faisait donc plus qu'un appel LLM complet (~8s dans nos
                # tests) par mission, pour un résultat jeté. Voir Advisor.prepare_advice() /
                # Learner.prepare_advice() : ces méthodes restent en place (utilité potentielle
                # pour un déclenchement manuel depuis le frontend), seul cet appel automatique
                # a été retiré.
                # ---> NOUVEAU : On prévient le C++ de préparer le Widget Mission Control
                await self.propagate_event(Events.MISSION_STARTED, {
                    "goal": refined_goal
                })
                
                # 4. Instanciation et isolation du Solver Racine avec le but raffiné
                self.root_solver = Solver(
                    solver_id="root",
                    goal=refined_goal, 
                    parent=self,
                    provider_manager=self.provider_manager,
                    runtime_state=self.runtime_state,
                    provider_id=forced_provider,
                    model_id=forced_model,
                )

                # 5. Exécution de la réflexion HTN
                try:
                    result = await self.root_solver.run()
                except Exception as e:
                    Logger.error(f"[Orchestrator] Erreur critique pendant l'exécution du Solver : {e}")
                    # Mise à jour du cache en erreur
                    if session_id in self.session_memories:
                        session_memory = self.session_memories[session_id]
                        mission_cache = session_memory.get_active_mission()
                        if mission_cache:
                            mission_cache.status = "failed"
                            mission_cache.finished_at = datetime.now()
                            mission_cache.execution_tree = self.root_solver.execution_tree if self.root_solver else None
                            mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry) if self.root_solver else {}
                            session_memory.context.last_mission_status = "failed"
                            session_memory.context.unresolved_issues.append(f"Mission {mission_cache.mission_id} interrompue suite a une erreur systeme!")
                    # On relève l'exception pour que le bloc parent la traite
                    raise

                # --- Sauvegarde de l'arbre d'exécution ---
                from core.execution_serializer import ExecutionSerializer

                # Récupération des métadonnées depuis le contexte
                session_id = self.current_execution_context.get("session_id")
                provider_id = self.current_execution_context.get("provider_id")
                model_id = self.current_execution_context.get("model_id")

                ExecutionSerializer.save_mission(
                    mission_id=self.root_solver.id,
                    goal=refined_goal,  # ou self.root_solver.goal
                    execution_tree=result.execution_tree,
                    resolved_data=self.root_solver.variable_registry,
                    status=result.status.value,
                    final_response=result.response if result.status == ExecutionStatus.SUCCESS else None,
                    final_context=result.final_context,
                    session_id=session_id,
                    provider_id=provider_id,
                    model_id=model_id,
                    # extra_metadata possibles
                    parent_step_id=self.root_solver.parent_step_id,
                    depth=self.root_solver.depth
                )# SÉCURITÉ DE SORTIE : Si l'utilisateur a annulé pendant les actions de l'agent

                # --- PHASE 1 : MISE À JOUR DU CACHE MISSION ---
                if session_id in self.session_memories:
                    session_memory = self.session_memories[session_id]
                    mission_cache = session_memory.get_active_mission()
                    
                    if mission_cache:
                        # 1. Attacher l'arbre d'exécution
                        mission_cache.execution_tree = result.execution_tree
                        
                        # 2. Copie profonde des données résolues
                        if self.root_solver and hasattr(self.root_solver, 'variable_registry'):
                            import copy
                            mission_cache.resolved_data = copy.deepcopy(self.root_solver.variable_registry)
                        
                        # 3. Statut et horodatage
                        mission_cache.finished_at = datetime.now()
                        if result.status == ExecutionStatus.SUCCESS:
                            mission_cache.status = "success"
                        elif self.runtime_state.cancel_requested:
                            mission_cache.status = "cancelled"
                        else:
                            mission_cache.status = "failed"
                        
                        # 4. Mise à jour du contexte global
                        session_memory.context.mission_history.append(mission_cache.mission_id)
                        session_memory.context.last_mission_status = mission_cache.status
                        session_memory.context.touch()
                        
                        # 5. Si échec, ajouter une note dans unresolved_issues (sans troncature)
                        if mission_cache.status in ("failed", "cancelled"):
                            issue = f"Mission {mission_cache.mission_id} terminée en {mission_cache.status}"
                            if result.error_reason:
                                issue += f" - {result.error_reason}"
                            session_memory.context.unresolved_issues.append(issue)
                        
                        Logger.info(f"[Orchestrator] ✅ Mission cache mis à jour : {mission_cache.mission_id} (status={mission_cache.status})")
                        
                        # --- PHASE 2 : SAUVEGARDE EN BASE DE DONNÉES (asynchrone) ---
                        try:
                            await asyncio.to_thread(
                                self.mission_store.save_episode,
                                mission_cache,
                                session_id,
                                self.runtime_state.environment  # <--- on passe l'environnement
                            )
                        except Exception as e:
                            Logger.error(f"[Orchestrator] Échec sauvegarde base : {e}")

                        # Logs debug (toujours affichés si niveau DEBUG)
                        Logger.debug(f"[Orchestrator] Contexte session : {session_memory.context.to_dict()}")
                        Logger.debug(f"[Orchestrator] Détail mission : {mission_cache.to_dict()}")
                
                if self.runtime_state.cancel_requested:
                    Logger.info("[Orchestrator] Stop demandé pendant le RootSolver, réponse finale ignorée.")
                    await self.propagate_event(Events.THINKING_FINISHED, {})
                    return ErrorPacket(type="error", message= _("Génération annulée"))


                # 6. Phase de Commit (Consolidation de la mémoire à la suite du Solver)
                if result.status == ExecutionStatus.SUCCESS:
                    
                    # =================================================================
                    # INTERCEPTION PAR LE PRÉSENTATEUR (Génération du résumé propre)
                    # =================================================================
                    try:
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
                        # ---> NOUVEAU : télémétrie Presentator (succès) — le Presentator existe enfin
                        # dans les données que le Learner peut lire, plus seulement dans les logs.
                        if mission_cache:
                            mission_cache.presentator_result = {"status": "success", "error_reason": None}
                            await asyncio.to_thread(
                                self.mission_store.update_presentator_result,
                                mission_cache.mission_id, mission_cache.presentator_result
                            )
                    except Exception as e:
                        Logger.error(f"[Orchestrator] ⚠️ Échec du Presentator. Motif: {e}")
                        final_response = ""
                        # ---> NOUVEAU : on capture aussi l'échec, c'est la donnée la plus utile pour le Learner
                        if mission_cache:
                            mission_cache.presentator_result = {"status": "failed", "error_reason": str(e)}
                            await asyncio.to_thread(
                                self.mission_store.update_presentator_result,
                                mission_cache.mission_id, mission_cache.presentator_result
                            )

                    # --- FALLBACK DE SÉCURITÉ ---
                    # Si le LLM a échoué (ou si le plan s'est terminé sur un tool_call sans texte)
                    if not final_response or not final_response.strip():
                        fallback_ctx = result.final_context[-500:] if result.final_context else "Aucun contexte disponible."
                        final_response = _("Mission achevée techniquement, mais le rapport final n'a pas pu être généré.\n\n**Dernier état :**\n```\n{}\n```").format(fallback_ctx)
                    # =================================================================
                    
                    # Consigne de l'interaction dans l'historique
                    self.memory.add_interaction(
                        session_id=session_id, 
                        user_msg=user_message, 
                        ai_msg=final_response, 
                        provider_id=forced_provider
                    )
                    
                    Logger.info(f"[Orchestrator] ✅ Interaction consolidée en mémoire pour la session {session_id}")
                    
                    # EMISSION OBLIGATOIRE (Évite le freeze du C++)
                    await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
        
                    return ResponsePacket(type="response", status="success", payload={"message": final_response})
                
                else:
                    Logger.warning(f"[Orchestrator] ⚠️ Résolution avortée. Aucun commit en mémoire. Raison : {result.error_reason}")
                    await self.propagate_event(Events.MISSION_FAILED, {"reason": result.error_reason})

                    # Générer un message d'échec digeste via le Presentator
                    try:
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
                        Logger.error(f"[Orchestrator] ⚠️ Échec du Presentator pour l'erreur : {e}")
                        final_response = _("❌ La mission a échoué : {}").format(result.error_reason)
                        if mission_cache:
                            mission_cache.presentator_result = {"status": "failed", "error_reason": str(e)}
                            await asyncio.to_thread(
                                self.mission_store.update_presentator_result,
                                mission_cache.mission_id, mission_cache.presentator_result
                            )

                    # Commit de l'échec dans l'historique
                    self.memory.add_interaction(
                        session_id=session_id,
                        user_msg=user_message,
                        ai_msg=final_response,
                        provider_id=forced_provider
                    )
                    Logger.info(f"[Orchestrator] Échec logique enregistré dans l'historique.")

                    await self.propagate_event(Events.RESPONSE_COMPLETED, {"content": final_response})
                    return ResponsePacket(type="response", status="success", payload={"message": final_response})

        except Exception as e:
            Logger.error(f"[Orchestrator] Critical failure during agent loop: {str(e)}")
            await self.propagate_event(Events.RUNTIME_ERROR, {"message": str(e)})
            return ErrorPacket(type="error", message=str(e))
        finally:
            await self.propagate_event(Events.THINKING_FINISHED, {})
            
    # =====================================================
    # IMPLÉMENTATION DE SUPERVISOR (Le sommet de la pyramide)
    # =====================================================
    async def validate_plan(self, plan: Plan, child_solver_id: str) -> bool:
        """
        Le PDG valide le plan global remonté par le RootSolver.
        Vérifie la convergence entre le plan tactique et l'objectif stratégique raffiné.
        """
        Logger.info(f"[Orchestrator] ⚖️ Validation du plan du Solver '{child_solver_id}'")
        
        # Récupération de l'objectif raffiné depuis le contexte d'exécution
        # (On fallback sur plan.goal si jamais le contexte n'était pas défini)
        target_goal = self.current_execution_context.get("refined_goal") or plan.goal
        
        Logger.info(f"[Orchestrator] [CONVERGENCE CHECK] Objectif cible attendu : '{target_goal}'")
        Logger.info(f"[Orchestrator] Plan proposé pour atteindre cet objectif :")
        for step in plan.steps:
            Logger.info(f"   -> Étape : {step.description} [{step.type.value}]")
            
        # =========================================================================
        # ÉTAPE FUTURE PRO : C'est ici que tu injecteras ton "LLM Judge" de sécurité.
        # Exemple conceptuel :
        # is_convergent = await self.judge_manager.check_plan_safety(target_goal, plan)
        # if not is_convergent: 
        #     Logger.warning("[Orchestrator] ❌ Plan rejeté : Non-convergence ou risque détecté.")
        #     return False
        # =========================================================================
        
        Logger.info("[Orchestrator] ✅ Le plan converge vers l'objectif raffiné. Feu vert pour exécution.")
        return True

    async def report_critical_failure(self, error_context: str, child_solver_id: str):
        """
        Intercepte une alerte critique qui a traversé tout l'arbre jusqu'au sommet.
        """
        Logger.error(f"[Orchestrator] 🚨 ALERTE CRITIQUE du Solver '{child_solver_id}' : {error_context}")
        # On émet un événement d'erreur pour que le frontend puisse afficher une popup ou stopper les actions en cours
        await self.propagate_event(Events.RUNTIME_ERROR, {"message": _("Alerte critique de la branche {}: {}").format(child_solver_id, error_context)})

    # =====================================================
    # RETOUR RESEAU FRONTEND -> ORCHESTRATOR
    # =====================================================
    async def _handle_tool_result(self, packet: RequestPacket):
        """
        Réceptionne le résultat brut de l'outil envoyé par le frontend.
        Débloque le Solver en attente.
        """
        payload = packet.payload
        call_id = payload.get("call_id")
        tool_result = payload.get("result", "") # Le contexte textuel ou JSON renvoyé par l'outil
        
        Logger.info(f"[Orchestrator] 📥 Retour matériel reçu du frontend pour l'ID: {call_id}")
        
        if call_id in self.pending_tool_calls:
            # On injecte le résultat dans le Future, ce qui réveille instantanément la ligne d'attente
            self.pending_tool_calls[call_id].set_result(tool_result)
            return ResponsePacket(type="response", status="success", payload={"message": _("Result routed to solver.")})
        else:
            Logger.error(f"[Orchestrator] Aucun solver en attente pour l'ID d'outil: {call_id}")
            return ErrorPacket(type="error", message=_("No pending context found for call_id: {}").format(call_id))
        
    async def _handle_chat_stop(self):
        Logger.info("[Orchestrator] 🛑 ARRET D'URGENCE DEMANDE PAR L'UI")
        self.runtime_state.cancel_requested = True
        
        # CORRECTIF DEADLOCK : Débloquer immédiatement tous les Solvers figés sur un outil
        for call_id, future in self.pending_tool_calls.items():
            if not future.done():
                # On simule une réponse de l'outil au format rigide JSON
                future.set_result(_('{"result": false, "message": "Exécution interrompue par l\'utilisateur."}'))
        
        self.pending_tool_calls.clear()

        return ResponsePacket(type="response", status="success", payload={"message": _("Stop signal broadcasted")})
    
    async def _handle_runtime_configure(self, packet: RequestPacket):
        Logger.info("Runtime configuration started")
        payload = packet.payload
        self.runtime_state.system_prompt = payload.get("system_prompt", "")
        self.runtime_state.language = payload.get("language", "en")
        # --- Environnement : un seul flag, contrôlé exclusivement par le front ---
        self.runtime_state.environment = payload.get("environment", "simulated")
        Logger.info(f"[Orchestrator] Environnement = {self.runtime_state.environment}")
         # ---> Initialisation de gettext avec la langue reçue <---
        from core.i18n import setup_i18n
        setup_i18n(self.runtime_state.language)
        # ------------------------------------------------------
        
        # ---> Initialisation et peuplement du ToolsManager <---
        from tools.tools_manager import ToolsManager
        
        # 1. On crée l'instance vide dans le state
        self.runtime_state.tools_manager = ToolsManager()
        
        # 2. On lui passe la liste brute envoyée par le C++ pour qu'il s'auto-configure
        raw_tools = payload.get("tools", [])
        self.runtime_state.tools_manager.load_tools_from_payload(raw_tools)
        
        # ------------------------------------------------------

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
            elif normalized_key == Providers.OPENAI:  # <- On ajoute le nouveau bloc ici
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
    
    # =====================================================
    # MÉTHODE DE SUPERVISION : EXÉCUTION DES OUTILS
    # =====================================================
    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Reçoit la demande d'outil du RootSolver, génère un call_id,
        notifie le frontend et se met en attente asynchrone stricte.
        """

        # ---> NOUVEAU : Validation par le ToolsManager
        is_valid = self.runtime_state.tools_manager.validate_tool_call(tool_name, arguments)
        
        if not is_valid:
            return json.dumps({"result": False, "data": None, "message": "Tool not found"})    
        
        import uuid
        call_id = str(uuid.uuid4())
        
        # Création d'un verrou asynchrone (Future)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_tool_calls[call_id] = future
        
        Logger.info(f"[Orchestrator] 📤 Dispatch de l'outil vers le frontend [{tool_name}] (ID: {call_id}) (Argument = {arguments})")
        
        # Émission de l'événement vers la couche de transport réseau pour le frontend
        # Remplace 'Events.REQUEST_RECEIVED' ou utilise une constante dédiée si tu en as une (ex: Events.TOOL_CALL_TRIGGERED)
        await self.propagate_event(Events.TOOL_REQUESTED, {
            "call_id": call_id,
            "tool_name": tool_name,
            "arguments": arguments
        })
        
        try:
            # L'orchestrateur suspend cette ligne d'exécution jusqu'à ce que .set_result() soit appelé
            result_context = await future
            return result_context
        finally:
            # Nettoyage de sécurité
            self.pending_tool_calls.pop(call_id, None)


    async def propagate_event(self, event_name: str, payload: dict):
        # Gestion du Heartbeat
        if event_name == Events.THINKING_STARTED:
            # Démarrer le heartbeat si ce n'est pas déjà fait
            if self._heartbeat_task is None or self._heartbeat_task.done():
                self._heartbeat_task = asyncio.create_task(self._send_heartbeat())
                Logger.debug("[Orchestrator] Heartbeat started.")
        
        elif event_name in (Events.THINKING_FINISHED, Events.RUNTIME_ERROR, Events.MISSION_FAILED):
            # Arrêter le heartbeat s'il est en cours
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None
                Logger.debug("[Orchestrator] Heartbeat stopped.")

        # Ajout du session_id (comme avant)
        if "session_id" not in payload and self.current_execution_context.get("session_id"):
            payload["session_id"] = self.current_execution_context["session_id"]
        
        await self.event_bus.emit(event_name, payload)


    async def _send_heartbeat(self):
        """Émet un événement HEARTBEAT toutes les 30 secondes tant que la mission est active."""
        try:
            while not self.runtime_state.cancel_requested:
                await asyncio.sleep(30)
                await self.propagate_event(Events.HEARTBEAT, {})
        except asyncio.CancelledError:
            Logger.debug("[Orchestrator] Heartbeat task cancelled.")
            raise
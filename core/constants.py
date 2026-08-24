try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass

class Actions:
    RUNTIME_CONFIGURE = "runtime.configure"
    CHAT_SEND = "chat.send"
    CHAT_STOP = "chat.stop"
    CHAT_RESET = "chat.reset"
    SESSION_DELETE = "session.delete"
    
    # NOUVEAU : Quand le C++ a fini d'exécuter un outil physique et renvoie le résultat
    TOOL_RESULT = "tool.result" 
    LEARNER_ANALYZE = "learner.analyze"   # <--- NOUVEAU

class Events:
    RUNTIME_READY = "runtime.ready"
    RUNTIME_CONFIGURED = "runtime.configured"
    RUNTIME_ERROR = "runtime.error"
    THINKING_STARTED = "thinking.started"
    THINKING_FINISHED = "thinking.finished"
    RESPONSE_CHUNK = "response.chunk"
    RESPONSE_COMPLETED = "response.completed"
    CONVERSATION_RESET = "conversation.reset"
    REQUEST_RECEIVED = "request.received"

    # NOUVEAUX : Le workflow interne de l'Entreprise Agentique (Hub & Spoke)
    PLANNER_START = "planner.start"           # PDG -> Stratège : "Analyse cette demande"
    TOOL_REQUESTED = "tool.requested"         # Stratège -> PDG : "Je suggère d'utiliser cet outil"
    EXECUTOR_RUN_TOOL = "executor.run_tool"   # PDG -> Ouvrier : "Demande au C++ d'exécuter ça"
    PLANNER_FINISHED = "planner.finished"     # Stratège -> PDG : "J'ai fini, voici la réponse texte"
    #STEP_STARTED = "step.started"
    MISSION_STARTED = "mission.started"
    PLAN_GENERATED = "plan.generated"
    STEP_STATUS_CHANGED = "step.status_changed"
    MISSION_FAILED = "mission.failed" # NOUVEAU : Rejet de faisabilité ou crash
    PLAN_ABANDONED = "plan.abandoned" # NOUVEAU : Signal qu'un plan généré a échoué et va être remplacé
    HEARTBEAT = "heartbeat"
    PLANNER_RETRY = "planner.retry"

    LEARNER_ANALYZE_STARTED = "learner.analyze_started"
    LEARNER_ANALYZE_FINISHED = "learner.analyze_finished"
    # Discovery Framework
    DISCOVERY_SESSION_START = "discovery.session_start"
    DISCOVERY_SESSION_END = "discovery.session_end"
    DISCOVERY_STEP = "discovery.step"
    DISCOVERY_CACHE_HIT = "discovery.cache_hit"

    # Dans Events
    DISCOVERY_PLAN_GENERATION_START = "discovery.plan_generation_start"
    DISCOVERY_PLAN_GENERATION_END = "discovery.plan_generation_end"
    DISCOVERY_PLAN_GENERATION_ERROR = "discovery.plan_generation_error"

    TOOLS_MANAGER_DECISION = "tools_manager.decision"
    TOOLS_MANAGER_EXECUTION = "tools_manager.execution"
    TOOLS_MANAGER_RESULT = "tools_manager.result"
    TOOLS_MANAGER_ERROR = "tools_manager.error"
    
class Providers:
    GEMINI = "gemini"
    GROQ = "groq"
    OPENAI = "openai"  # <- On ajoute ça ici
    OPENROUTER = "openrouter"

class OrchestratorMode(StrEnum):
    DIRECT = "direct"
    MISSION = "mission"
    REQUEST = "request"   # <-- AJOUT

# =====================================================
# PARAMÈTRES DE RETRIEVAL
# =====================================================
RETRIEVAL_TOP_K = 20               # Nombre de voisins à récupérer
RETRIEVAL_THRESHOLD =  0.6         # Seuil de similarité pour filtrer les résultats
RETRIEVAL_MAX_RESULTS_INJECTED = 5 # Nombre max de missions similaires injectées dans le prompt

# =====================================================
# CACHE
# =====================================================
CACHE_MAX_ENTRIES = 1000
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 jours

# =====================================================
# ENTITY LEARNER
# =====================================================
ENTITY_LEARNER_MIN_EVIDENCE = 3   # Nombre minimal d'évidences pour consolider un groupe

# =====================================================
# DISCOVERY FRAMEWORK
# =====================================================
DISCOVERY_MAX_ITERATIONS = 10      # Nombre maximum d'étapes dans une DiscoverySession
DISCOVERY_CACHE_TTL = 7 * 24 * 3600  # 7 jours pour le cache des RefinedContexts

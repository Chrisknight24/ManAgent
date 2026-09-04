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
    HOST_MANIFEST_REGISTER = "host.manifest.register"  # <--- Enregistrement dynamique du HostManifest
    SYSTEM_WARMUP = "system.warmup"
    SYSTEM_RESET_DATA = "system.reset_data"
    DATA_STATS = "data.stats"
    DATA_PURGE = "data.purge"
    DATA_EXPORT = "data.export"

class Events:
    RUNTIME_READY = "runtime.ready"
    RUNTIME_CONFIGURED = "runtime.configured"
    RUNTIME_ERROR = "runtime.error"
    THINKING_STARTED = "thinking.started"
    STATUS_UPDATE = "status.update"
    THINKING_FINISHED = "thinking.finished"
    RESPONSE_CHUNK = "response.chunk"
    RESPONSE_COMPLETED = "response.completed"
    CONVERSATION_RESET = "conversation.reset"
    REQUEST_RECEIVED = "request.received"

    # Événements du cycle de vie des Skills & Host Protocol
    HOST_MANIFEST_UPDATED = "host.manifest_updated"
    CHECKPOINT_REACHED = "checkpoint.reached"
    BREAKOUT_OCCURRED = "breakout.occurred"
    EXECUTION_COMPLETED = "execution.completed"
    SKILL_STATE_CHANGED = "skill.state_changed"

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
    CLAUDE = "claude"
    DEEPSEEK = "deepseek"

class ModelCapabilities(StrEnum):
    TEXT = "text"
    VISION = "vision"
    TOOLS = "tools"
    STRUCTURED_OUTPUT = "structured_output"
    AUDIO = "audio"
    VIDEO = "video"
    REASONING = "reasoning"
    CODING = "coding"
    AGENTIC = "agentic"
    LONG_CONTEXT = "long_context"
    FAST_INFERENCE = "fast_inference"
    LOW_LATENCY = "low_latency"
    HIGH_VOLUME = "high_volume"
    CLASSIFICATION = "classification"
    DATA_EXTRACTION = "data_extraction"
    CYBERSECURITY = "cybersecurity"
    FILES = "files"
    COMPUTER_USE = "computer_use"
    AUTOMATIC_MODEL_SELECTION = "automatic_model_selection"
    DYNAMIC_ROUTING = "dynamic_routing"
    VISION_WHEN_AVAILABLE = "vision_when_available"

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
# ENTITY LEARNER & LESSON STORE
# =====================================================
ENTITY_LEARNER_MIN_EVIDENCE = 3      # Nombre minimal d'évidences pour consolider un groupe
LESSON_STORE_TOP_K = 3               # Nombre max de leçons similaires retournées par défaut
LESSON_SIMILARITY_THRESHOLD = 0.15   # Seuil de similarité cosinus pour les leçons
LESSON_MAX_KEYWORDS_PER_CALL = 6     # Nombre max de mots-clés par appel
LESSON_MAX_KEYWORDS_TOTAL = 20       # Nombre max total de mots-clés
LESSON_MAX_SOURCE_EPISODES = 50      # Nombre max d'épisodes sources par leçon

# =====================================================
# DISCOVERY FRAMEWORK & ASSETS
# =====================================================
DISCOVERY_MAX_ITERATIONS = 10        # Nombre maximum d'étapes dans une DiscoverySession
DISCOVERY_CACHE_TTL = 7 * 24 * 3600  # 7 jours pour le cache des RefinedContexts
DISCOVERY_MAX_SLICE_CHARS = 50000    # Budget large pour tranches de code/logs dans FilesExplorer
ASSET_INLINE_LIMIT = 3000            # Seuil de caractères au-delà duquel un résultat est encapsulé en DataAsset

# =====================================================
# SOLVER & EXÉCUTION
# =====================================================
SOLVER_MAX_DEPTH = 12                # Profondeur maximale de décomposition récursive
SOLVER_MAX_EXECUTION_TRIES = 3       # Nombre maximal de tentatives d'exécution d'un plan
SOLVER_MAX_PREEXECUTION_FAILURES = 3 # Nombre maximal d'échecs de validation de plan consécutifs
MAX_DEPTH_EXTENSIONS = 5             # Plafond d'extensions de profondeur accordées par mission (arbitrées par le Superviseur)
MAX_INSIGHTS_PER_TARGET = 5          # Nombre max d'insights mémorisés par cible

# =====================================================
# SKILL ENGINE
# =====================================================
SKILL_DISCOVERY_THRESHOLD = 2        # Nombre de succès consécutifs requis pour déclencher la création d'un Skill (DRAFT -> SHADOW)
SKILL_SHADOW_SUCCESS_THRESHOLD = 1   # Nombre de validations passives en SHADOW requises pour la promotion en PRODUCTION (Total = 3 répétitions réussies)
SKILL_CIRCUIT_BREAKER_MAX_FAILURES = 3 # Nombre d'échecs consécutifs en PRODUCTION avant QUARANTINE

# =====================================================
# LLM & CONTEXT BUDGETS
# =====================================================
LLM_STRUCTURED_MAX_ATTEMPTS = 2      # Nombre maximal de retries en cas d'erreur de schéma Pydantic
LLM_DISCOVERY_MAX_ITERATIONS = 5     # Nombre maximal d'itérations pour la Progressive Disclosure LLM
CONTEXT_MAX_TOTAL_TOKENS = 12000     # Budget total maximal de tokens pour l'Orchestrateur
CONTEXT_MAX_RECENT_TOKENS = 4000     # Budget de tokens pour les messages récents verbatim
CONTEXT_MAX_ASSETS_TOKENS = 2000     # Budget de tokens pour le manifeste des DataAssets
CONTEXT_MAX_FACTS_TOKENS = 1500      # Budget de tokens pour les faits et leçons sémantiques
CONTEXT_MAX_TIMELINE_TOKENS = 1000   # Budget de tokens pour l'index chronologique
CONTEXT_RECENT_TURNS_LIMIT = 6       # Nombre de tours récents inclus par défaut


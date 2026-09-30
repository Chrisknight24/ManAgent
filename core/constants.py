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
    SET_SKILL_STATE = "skill.set_state"
    REPAIR_SKILL = "skill.repair"
    SKILLS_LIST_REQUEST = "skill.list_request"
    SKILL_PAYLOAD_REQUEST = "skill.payload_request"
    EXPORT_SKILL_PACKAGE = "skill.export_package"
    IMPORT_SKILL_PACKAGE = "skill.import_package"
    EMBEDDINGS_CATALOG = "embeddings.catalog"
    EMBEDDINGS_PREPARE = "embeddings.prepare"
    EMBEDDINGS_SET_DEFAULT = "embeddings.set_default"
    EMBEDDINGS_CANCEL = "embeddings.cancel"
    STATS_GET = "stats.get"
    RULES_GET = "rules.get"
    RULES_SET = "rules.set"

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
MAX_RETRY_EXTENSIONS = 2             # Rallonges d'exécution accordables par mission (juge, jamais sans progrès)
MAX_DEPTH_EXTENSIONS = 5             # Plafond d'extensions de profondeur accordées par mission (arbitrées par le Superviseur)
MAX_INSIGHTS_PER_TARGET = 5          # Nombre max d'insights mémorisés par cible

# =====================================================
# SKILL ENGINE
# =====================================================
SKILL_DISCOVERY_THRESHOLD = 2        # Nombre de succès consécutifs requis pour déclencher la création d'un Skill (DRAFT -> SHADOW)
SKILL_SHADOW_SUCCESS_THRESHOLD = 1   # Nombre de validations passives en SHADOW requises pour la promotion en PRODUCTION (Total = 3 répétitions réussies)
SKILL_SHADOW_MISMATCH_THRESHOLD = 3  # Nombre de divergences consécutives d'un Skill en SHADOW sur missions réussies avant obsolescence / QUARANTINE
SKILL_CIRCUIT_BREAKER_MAX_FAILURES = 3 # Nombre d'échecs consécutifs en PRODUCTION avant QUARANTINE
SKILL_MAX_REPAIRS = 3                # Nombre max de réparations auto par skill avant RETIRED (None = infini, choix hôte)
SKILL_CHAMPION_MARGIN = 0.05         # Avance de confiance exigée pour qu'une vN+1 remplace la version en PRODUCTION

# Défauts affichés au contrat hôte (docs/HOST_CONTRACT.md §7c). L'hôte peut
# tout resserrer/desserrer via `skill_governance` (manifeste) : ces valeurs
# ne sont que le point de départ, jamais une décision à sa place.
SKILL_GOVERNANCE_DEFAULTS = {
    "discovery_threshold": SKILL_DISCOVERY_THRESHOLD,
    "shadow_success_threshold": SKILL_SHADOW_SUCCESS_THRESHOLD,
    "shadow_mismatch_threshold": SKILL_SHADOW_MISMATCH_THRESHOLD,
    "circuit_breaker_max_failures": SKILL_CIRCUIT_BREAKER_MAX_FAILURES,
    "max_repairs": SKILL_MAX_REPAIRS,
    "champion_margin": SKILL_CHAMPION_MARGIN,
}

# =====================================================
# LLM & CONTEXT BUDGETS
# =====================================================
LLM_STRUCTURED_MAX_ATTEMPTS = 2      # Nombre maximal de retries en cas d'erreur de schéma Pydantic
LLM_DISCOVERY_MAX_ITERATIONS = 5     # Nombre maximal d'itérations pour la Progressive Disclosure LLM
DISCOVERY_LOOP_HARD_MAX = 20         # Plafond absolu : l'hôte peut monter jusqu'ici, jamais au-delà
LLM_STRUCTURED_MAX_OUTPUT_TOKENS = 8192  # Plafond de sortie structurée (fail-fast : un plan tient en quelques Ko)
CONTEXT_MAX_TOTAL_TOKENS = 12000     # Budget total maximal de tokens pour l'Orchestrateur
CONTEXT_MAX_RECENT_TOKENS = 4000     # Budget de tokens pour les messages récents verbatim
CONTEXT_MAX_ASSETS_TOKENS = 2000     # Budget de tokens pour le manifeste des DataAssets
CONTEXT_MAX_FACTS_TOKENS = 1500      # Budget de tokens pour les faits et leçons sémantiques
CONTEXT_MAX_TIMELINE_TOKENS = 1000   # Budget de tokens pour l'index chronologique
CONTEXT_RECENT_TURNS_LIMIT = 6       # Nombre de tours récents inclus par défaut

# =====================================================
# PROTOCOLE (version du langage hôte <-> cerveau)
# =====================================================
# À monter à chaque changement incompatible (action/payload/event supprimé
# ou renommé). L'hôte déclare sa version dans runtime.configure ;
# mismatch = erreur bruyante, pas de session.
PROTOCOL_VERSION = "1"


def check_protocol_version(provided) -> tuple:
    """Vérifie la version annoncée par l'hôte.

    Retour : (accepted: bool, message: str).
    - Absent/vide : accepté + avertissement (transition vieux hôtes).
    - Égal : accepté.
    - Différent : refusé (erreur bruyante).
    Fonction pure (sans imports) pour rester testable vite.
    """
    if provided is None or (isinstance(provided, str) and not provided.strip()):
        return True, (
            "protocol_version absent — accepté en transition, "
            f"merci d'envoyer \"protocol_version\": \"{PROTOCOL_VERSION}\"."
        )
    if str(provided).strip() == PROTOCOL_VERSION:
        return True, "protocol version OK."
    return False, (
        f"protocol version mismatch (host:{provided} brain:{PROTOCOL_VERSION}) — "
        "mettez à jour le côté le plus ancien, session refusée."
    )


def clamp_discovery_limit(value, default: int) -> tuple:
    """Borne un plafond PD demandé par l'hôte : 1..DISCOVERY_LOOP_HARD_MAX.

    Retour : (limite: int, source: str). Source = 'defaut' si absent/invalide,
    'hôte' si repris. Fonction pure, testée vite.
    """
    try:
        v = int(value)
    except Exception:
        return int(default), "defaut"
    if v < 1:
        return 1, "hôte"
    if v > DISCOVERY_LOOP_HARD_MAX:
        return DISCOVERY_LOOP_HARD_MAX, "hôte"
    return v, "hôte"


def discovery_limits_from_runtime(runtime_state) -> tuple:
    """Plafonds PD effectifs (boucle LLM, étapes session) + sources.

    Lis depuis runtime_state.discovery_max_iterations /
    discovery_max_session_steps (posés par runtime.configure).
    Retour : (loop_max, session_max, loop_source, session_source).
    """
    loop_raw = getattr(runtime_state, "discovery_max_iterations", None) if runtime_state else None
    sess_raw = getattr(runtime_state, "discovery_max_session_steps", None) if runtime_state else None
    loop_max, loop_src = clamp_discovery_limit(loop_raw, LLM_DISCOVERY_MAX_ITERATIONS)
    session_max, sess_src = clamp_discovery_limit(sess_raw, DISCOVERY_MAX_ITERATIONS)
    return loop_max, session_max, loop_src, sess_src


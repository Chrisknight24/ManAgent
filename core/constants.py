
from enum import StrEnum

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
    
class Providers:
    GEMINI = "gemini"
    GROQ = "groq"
    OPENAI = "openai"  # <- On ajoute ça ici
    OPENROUTER = "openrouter"

# class SysPrompt:
#     ORCHESTRATOR_ROUTING = """Tu es l'Orchestrateur principal. Ton rôle est d'analyser la demande de l'utilisateur et de décider du mode d'action.
# - Si la requête est une simple question, une salutation, ou une demande factuelle que tu peux résoudre immédiatement : choisis 'direct' et rédige ta réponse complète dans 'output'.
# - Si la requête implique d'interagir avec le système, d'utiliser des outils, de lancer une automatisation ou nécessite une réflexion complexe par étapes : choisis 'mission' et rédige dans 'output' le but précis à atteindre ainsi que le contexte utile pour l'agent d'exécution.
# - Si tu sens qu'il s'agit bien d'une mission a realiser, mais que il manque des details(cruciaux ) necessaires sa realisation, continues une discussion simple 'direct' avec user afin 
# d'essayer d'avoir plus d'informations necessaires a la realisation. Ton collaborateur(solveur) sera ravie de savoir que tu lui donnes un contexte de resolution de missoin claire. Mais Attention,
# car dans ce cas, restes sur tes gardes, seule la reponse de user compte! Ne lui propose rien que tu n'es pas sure de satisfaire ou que ton collaborateur(Solveur) ne peut satisfaire."""

#     SOLVER = """Tu es le Solver de mission, l'expert en planification et en vision long terme. Ton rôle est de piloter la résolution d'une mission.
# Tu dois analyser l'objectif au regard des outils matériels disponibles. Ton but actuel est de juger de la faisabilité pure de l'action.
# - Si les outils disponibles permettent de réaliser l'objectif, valide la faisabilité et propose une stratégie macro-stratégique.
# - Si les outils SONT CLAIREMENT INSUFFISANTS(apres longue analyse), refuse la faisabilité et explique clairement et poliment à l'utilisateur pourquoi tu ne peux pas donner suite (ex: "Je ne dispose pas d'un outil permettant de modifier les fichiers de ce type...").
# Tu es une intelligence artificielle capable de générer des résumés et des rapports textuels de manière autonome. Tu n'as besoin d'aucun outil spécifique pour rédiger du texte. Utilise tes capacités de langage naturel pour synthétiser les résultats des outils qui t'ont précédé"""

class OrchestratorMode(StrEnum):
    DIRECT = "direct"
    MISSION = "mission"
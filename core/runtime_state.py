"""
runtime_state.py
================
Stockage central de l'état runtime. Évite les variables globales 
et permet la configuration dynamique depuis le frontend Qt.
"""
from core.i18n import _
from typing import Dict,List, Optional
from core.execution_context import ExecutionContext
from embeddings.manager import EmbeddingProviderManager
from utils.logger import Logger
class RuntimeState:
    """État central du runtime."""
    def __init__(self):
        self.system_prompt = _("You are a helpful AI assistant.")
        self.is_configured = False
        self.tools_manager = None
        self.cancel_requested: bool = False  # Le vrai Kill Switch
        self.language = "en"

        # --- PHASE 3 & 4 : Apprentissage et environnement ---
        self.learner = None  # Instance du Learner (initialisée dans orchestrator)
        # Un seul flag, gouvernant à la fois ce qui est ÉCRIT (tag des épisodes) et ce qui est
        # LU (filtre des leçons injectées) — toujours identiquement, sans exception ni override.
        # "real" est une étiquette de confiance décidée consciemment côté front, pas une
        # promesse technique sur la fiabilité des outils C++ : ne la passer à "real" que quand
        # ils ne sont plus des générateurs aléatoires de test.
        self.environment: str = "simulated"  # 'simulated' ou 'real'
        self.session_memory = None   # Référence à SessionMemory (définie dans orchestrator)
        self.presentator_detail_level = "brief"   # "brief" ou "detailed"
        self.current_signatures = []  # Stockage des signatures de la mission en cours
        self.solver_registry: Dict[str, Dict] = {}
            # Clé = solver_id (string)
            # Valeur = {
            #   "signatures": List[MissionSignature],
            #   "similar_missions": Optional[List[Dict]]  # résultat du retriever (mis en cache)
            # }
        self.current_mission_id = None  # Stockage du mission_id de la mission en cours
        self.execution_context = ExecutionContext()
        self.embedding_manager = EmbeddingProviderManager()
        self.active_embedding_model: Optional[str] = None  # ID du modèle choisi par l'user

        self.embedding_manager = EmbeddingProviderManager()
        self.active_embedding_model: Optional[str] = None  # ID du modèle actif
        self.auto_learn_enabled = True

        self.execution_markers = {
            "execution_attempt": 0,
            "has_abstract_task": False,
            "plan_rejected": False,
            "is_novel": False,
        }
    
    def reset_execution_markers(self):
        """Réinitialise les marqueurs d'exécution pour une nouvelle mission."""
        self.execution_markers = {
            "execution_attempt": 0,
            "has_abstract_task": False,
            "plan_rejected": False,
            "is_novel": False,
        }

    def update_marker(self, key: str, value, mode='set'):
        """
        Met à jour un marqueur de manière cumulative.
        - 'execution_attempt' : prend le max
        - 'has_abstract_task', 'plan_rejected', 'is_novel' : OR logique
        - autre : assignation directe (fallback)
        """
        if key == 'execution_attempt':
            current = self.execution_markers.get(key, 0)
            self.execution_markers[key] = max(current, value)
        elif key in ('has_abstract_task', 'plan_rejected', 'is_novel'):
            if value:
                self.execution_markers[key] = True
            # Si value est False, on ne fait rien (ne jamais désactiver un flag)
        else:
            self.execution_markers[key] = value

        Logger.debug(f"[RuntimeState] update_marker: {key} = {value} (current: {self.execution_markers.get(key)})")

        
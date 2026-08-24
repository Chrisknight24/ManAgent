"""
runtime_state.py
================
Stockage central de l'état runtime. Évite les variables globales 
et permet la configuration dynamique depuis le frontend Qt.
"""
from core.i18n import _
from typing import Dict, List, Optional, Any
from core.execution_context import ExecutionContext
from embeddings.manager import EmbeddingProviderManager
from utils.logger import Logger

class RuntimeState:
    """État central du runtime."""
    def __init__(self):
        self.system_prompt = _("You are a helpful AI assistant.")
        self.is_configured = False
        self.tools_manager = None
        self.cancel_requested: bool = False
        self.generation_epoch: int = 0
        self.language = "en"

        # --- PHASE 3 & 4 : Apprentissage et environnement ---
        self.learner = None
        self.environment: str = "simulated"  # 'simulated' ou 'real'
        self.session_memory = None
        self.presentator_detail_level = "brief"   # "brief" ou "detailed"
        self.current_signatures = []
        self.solver_registry: Dict[str, Dict] = {}
        self.current_mission_id = None
        self.depth_extensions_granted: Dict[str, int] = {}
        self.execution_context = ExecutionContext()
        self.embedding_manager = EmbeddingProviderManager()
        self.active_embedding_model: Optional[str] = None

        self.auto_learn_enabled = True

        self.execution_markers = {
            "execution_attempt": 0,
            "has_abstract_task": False,
            "plan_rejected": False,
            "is_novel": False,
        }

        # --- DISCOVERY FRAMEWORK ---
        self.cache_manager = None
        self.discovery_llm = None
        self.discovery_engine = None            
        
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
        else:
            self.execution_markers[key] = value

        Logger.debug(f"[RuntimeState] update_marker: {key} = {value} (current: {self.execution_markers.get(key)})")

    def set_discovery_llm(self, llm):
        """Définit le LLM à utiliser pour les SemanticTools du Discovery Framework."""
        self.discovery_llm = llm

    def set_discovery_engine(self, engine):
        """Définit le DiscoveryEngine."""
        self.discovery_engine = engine

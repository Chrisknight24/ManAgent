"""
runtime_state.py
================
Stockage central de l'état runtime. Évite les variables globales 
et permet la configuration dynamique depuis le frontend Qt.
"""
from core.i18n import _
from typing import Dict,List
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
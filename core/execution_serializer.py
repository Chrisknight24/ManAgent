import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from utils.logger import Logger
from core.i18n import _

class ExecutionSerializer:
    """
    Sérialise l'arbre d'exécution d'une mission dans un fichier JSON.
    """

    BASE_DIR = Path("execution_trees")

    @classmethod
    def _ensure_dir(cls):
        cls.BASE_DIR.mkdir(exist_ok=True)

    @classmethod
    def _generate_filename(cls, mission_id: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # avec millisecondes
        return cls.BASE_DIR / f"tree_{mission_id}_{timestamp}.json"

    @classmethod
    def save_mission(cls,
                     mission_id: str,
                     goal: str,
                     execution_tree: Optional[Any],
                     resolved_data: Optional[Dict[str, Any]],
                     status: str,
                     final_response: Optional[str] = None,
                     final_context: Optional[str] = None,
                     session_id: Optional[str] = None,
                     provider_id: Optional[str] = None,
                     model_id: Optional[str] = None,
                     **extra_metadata) -> Optional[Path]:
        """
        Sauvegarde l'intégralité des données de la mission dans un fichier JSON.

        Args:
            mission_id: identifiant du solver root
            goal: objectif de la mission
            execution_tree: objet ExecutionTree (ou None)
            resolved_data: dictionnaire des variables résolues (variable_registry)
            status: statut final (success, failed)
            final_response: réponse finale générée par le présentateur
            final_context: contexte final accumulé
            session_id: identifiant de session
            provider_id: fournisseur utilisé
            model_id: modèle utilisé
            extra_metadata: champs additionnels

        Returns:
            Path du fichier créé, ou None si erreur
        """
        cls._ensure_dir()

        payload = {
            "mission_id": mission_id,
            "goal": goal,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "final_response": final_response,
            "final_context": final_context,
            "resolved_data": resolved_data,
            "execution_tree": execution_tree.model_dump(mode='json') if execution_tree else None,
            "extra": extra_metadata
        }

        # Nettoyer les champs None pour alléger le fichier
        # (optionnel, on peut garder None pour lisibilité)
        # payload = {k: v for k, v in payload.items() if v is not None}

        filename = cls._generate_filename(mission_id)
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            Logger.info(_("[Serializer] ✅ Arbre sauvegardé dans : {}").format(filename))
            return filename
        except Exception as e:
            Logger.error(_("[Serializer] ❌ Échec de sauvegarde : {}").format(str(e)))
            return None
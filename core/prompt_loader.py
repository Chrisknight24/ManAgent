"""
core/prompt_loader.py
=====================
Chargeur de prompts avec templating Jinja2 et support multilingue.
"""

import os
from typing import Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from utils.logger import Logger

class PromptLoader:
    """
    Charge les prompts depuis des fichiers Markdown avec Jinja2.
    Supporte le multi-langues via un sous-dossier par langue.
    """
    
    def __init__(self, base_dir: str = None, default_lang: str = "en"):
        # Si base_dir n'est pas fourni, on le calcule par rapport à ce fichier
        if base_dir is None:
            # On remonte d'un niveau depuis core/ pour arriver à la racine du projet
            core_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(core_dir)
            base_dir = os.path.join(project_root, "prompts")
        self.base_dir = base_dir
        Logger.info(f"base dir = {self.base_dir}")
        
        self.default_lang = default_lang
        self.env_cache: Dict[str, Environment] = {}
        
    def _get_env(self, lang: str) -> Environment:
        """Retourne l'environnement Jinja2 pour une langue donnée."""
        if lang not in self.env_cache:
            # Priorité : langue spécifique -> base
            template_dirs = [
                os.path.join(self.base_dir, lang),
                os.path.join(self.base_dir, "base")
            ]
            # Filtrer les dossiers qui existent
            existing_dirs = [d for d in template_dirs if os.path.isdir(d)]
            
            self.env_cache[lang] = Environment(
                loader=FileSystemLoader(existing_dirs),
                autoescape=False,                     # <--- CORRECTION
                trim_blocks=True,
                lstrip_blocks=True
            )
        return self.env_cache[lang]
    
    def load(self, template_name: str, lang: Optional[str] = None, **kwargs) -> str:
        """
        Charge un template et le rend avec les variables fournies.
        
        :param template_name: Nom du fichier (ex: 'planner.md')
        :param lang: Code langue (ex: 'fr', 'en'). Si None, utilise default_lang.
        :param kwargs: Variables à injecter dans le template.
        :return: Prompt final rendu.
        """
        if lang is None:
            lang = self.default_lang
            
        env = self._get_env(lang)
        try:
            template = env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            # Fallback sur la langue par défaut si le template n'existe pas
            if lang != self.default_lang:
                env = self._get_env(self.default_lang)
                template = env.get_template(template_name)
                return template.render(**kwargs)
            raise e

# Instance singleton
_loader: Optional[PromptLoader] = None

def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
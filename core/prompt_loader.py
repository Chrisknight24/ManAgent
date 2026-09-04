"""
core/prompt_loader.py
=====================
Chargeur de prompts avec templating Jinja2 et support multilingue.
"""

import os
import re
from typing import Dict, Any, Optional

try:
    from jinja2 import Environment, FileSystemLoader
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False
    Environment = None
    FileSystemLoader = None

from utils.logger import Logger

class PromptLoader:
    """
    Charge les prompts depuis des fichiers Markdown avec Jinja2 (ou un fallback basique).
    Supporte le multi-langues via un sous-dossier par langue.
    """
    
    def __init__(self, base_dir: str = None, default_lang: str = "fr"):
        # IDE sync
        if base_dir is None:
            core_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(core_dir)
            base_dir = os.path.join(project_root, "prompts")
        self.base_dir = base_dir
        self.default_lang = default_lang
        self.env_cache: Dict[str, Any] = {}
        
    def _get_env(self, lang: str):
        """Retourne l'environnement Jinja2 pour une langue donnée."""
        if not JINJA2_AVAILABLE:
            return None
            
        if lang not in self.env_cache:
            template_dirs = [
                os.path.join(self.base_dir, lang),
                os.path.join(self.base_dir, "base"),
                self.base_dir
            ]
            existing_dirs = [d for d in template_dirs if os.path.isdir(d)]
            
            self.env_cache[lang] = Environment(
                loader=FileSystemLoader(existing_dirs),
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True
            )
        return self.env_cache[lang]

    def _fallback_render(self, template_name: str, lang: str, kwargs: Dict[str, Any]) -> str:
        """Fallback basique si Jinja2 n'est pas disponible."""
        template_dirs = [
            os.path.join(self.base_dir, lang),
            os.path.join(self.base_dir, "base"),
            self.base_dir
        ]
        
        content = None
        for d in template_dirs:
            p = os.path.join(d, template_name)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                break
                
        if content is None:
            raise FileNotFoundError(f"Template {template_name} non trouvé dans {template_dirs}")
            
        # Remplacement très basique des {{ var }}
        for k, v in kwargs.items():
            content = re.sub(r'\{\{\s*' + re.escape(k) + r'\s*\}\}', str(v), content)
            
        # Suppression basique des blocs if
        content = re.sub(r'\{%.*?%\}', '', content)
        
        return content

    def load(self, template_name: str, lang: Optional[str] = None, **kwargs) -> str:
        """
        Charge un template et le rend avec les variables fournies.
        """
        if lang is None:
            lang = self.default_lang
            
        if not JINJA2_AVAILABLE:
            try:
                return self._fallback_render(template_name, lang, kwargs)
            except Exception as e:
                if lang != self.default_lang:
                    try:
                        return self._fallback_render(template_name, self.default_lang, kwargs)
                    except Exception:
                        pass
                Logger.error(f"[PromptLoader] Impossible de charger '{template_name}' via fallback : {e}")
                return f"Prompt template '{template_name}'"
                
        env = self._get_env(lang)
        try:
            template = env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            if lang != self.default_lang:
                try:
                    env_default = self._get_env(self.default_lang)
                    template = env_default.get_template(template_name)
                    return template.render(**kwargs)
                except Exception:
                    pass
            Logger.error(f"[PromptLoader] Impossible de charger '{template_name}' via jinja2 : {e}")
            return f"Prompt template '{template_name}'"

_loader: Optional[PromptLoader] = None

def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader

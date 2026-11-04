"""
core/i18n.py
============
Module de gestion des traductions avec gettext.
"""

import gettext
import os
from typing import Optional

_translation: Optional[gettext.NullTranslations] = None
_DOMAIN = "messages"   # changez ici pour votre propre nom de domaine

def setup_i18n(lang: str = "en") -> None:
    """
    Initialise gettext avec la langue spécifiée.
    La variable d'environnement LANGUAGE peut aussi être utilisée.
    Si la langue n'est pas disponible, utilise NullTranslations (fallback).
    """
    global _translation
    try:
        # Le dossier locale est à la racine du projet (un niveau au-dessus de core/)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        locale_dir = os.path.join(root, "locale")
        # Chaîne réelle : sans .mo, gettext retombe silencieusement sur le
        # texte source. On compile au démarrage si le catalogue est absent/ancien.
        try:
            from core.i18n_compiler import ensure_catalogs
            ensure_catalogs(
                locale_dir, root,
                languages=tuple(dict.fromkeys((lang, "en", "fr"))),
            )
        except Exception:
            pass
        _translation = gettext.translation(
            _DOMAIN,
            localedir=locale_dir,
            languages=[lang],
            fallback=True
        )
    except Exception:
        _translation = gettext.NullTranslations()

def _(text: str) -> str:
    """
    Fonction de traduction raccourcie.
    Si aucune traduction n'est trouvée, retourne le texte original.
    """
    if _translation is None:
        return text
    return _translation.gettext(text)
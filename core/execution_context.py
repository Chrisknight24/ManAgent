"""
execution_context.py
====================
Contexte d'exécution courant (stack) pour l'observabilité.
"""

from contextlib import contextmanager
from typing import Optional, Dict, Any
import uuid


class ExecutionContext:
    """
    Représente le contexte d'exécution courant.
    Porté par RuntimeState.
    """
    def __init__(self):
        self._stack = []
        self._span_stack = []  # pile des span_id actifs

    @contextmanager
    def scope(self, **kwargs):
        old_context = self._stack[-1].copy() if self._stack else {}
        new_context = old_context.copy()
        new_context.update(kwargs)

        # Générer un span_id pour ce nouveau contexte (sauf si déjà fourni)
        if "span_id" not in new_context:
            new_context["span_id"] = str(uuid.uuid4())
        # Le parent_span_id est l'ancien span_id au sommet de la pile
        if self._span_stack:
            new_context["parent_span_id"] = self._span_stack[-1]
        else:
            new_context["parent_span_id"] = None

        self._stack.append(new_context)
        self._span_stack.append(new_context["span_id"])
        try:
            yield
        finally:
            self._stack.pop()
            self._span_stack.pop()


    def push(self, **kwargs):
        """Version sans context manager (pour les cas où on ne peut pas utiliser 'with')."""
        old_context = self._stack[-1].copy() if self._stack else {}
        new_context = old_context.copy()
        new_context.update(kwargs)
        self._stack.append(new_context)

    def pop(self):
        """Restaure le contexte précédent."""
        if self._stack:
            self._stack.pop()

    def to_dict(self) -> Dict[str, Any]:
        """Retourne une copie du contexte courant (au sommet de la pile)."""
        if self._stack:
            return self._stack[-1].copy()
        return {}

    def get(self, key: str, default=None):
        return self.to_dict().get(key, default)

    def __getattr__(self, name):
        val = self.get(name)
        if val is not None:
            return val
        raise AttributeError(f"'{name}' not in context")
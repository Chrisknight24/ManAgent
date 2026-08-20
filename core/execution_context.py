"""
execution_context.py
====================
Contexte d'exécution courant (pile de contextes imbriqués) pour l'observabilité.

Version robuste : la pile est portée par une contextvars.ContextVar plutôt que
par une liste Python mutable sur l'instance.

Pourquoi ce changement :
-------------------------
L'implémentation précédente stockait la pile dans `self._stack` (une simple
liste), partagée par TOUTE l'application puisque RuntimeState (et donc
ExecutionContext) est une instance unique pour tout le process. Tant que le
code reste strictement séquentiel (une seule chaîne d'`await`, jamais deux
`scope()` actifs "en même temps"), ça fonctionne. Mais dès qu'une exécution
concurrente apparaît (asyncio.gather, plusieurs tasks créées avec
create_task, deux Discovery ou deux sous-solvers lancés en parallèle...),
une liste partagée se corrompt : la tâche A peut pousser un contexte, céder
la main le temps d'un `await`, et voir la tâche B pousser PAR-DESSUS le
contexte de A avant qu'elle ne reprenne la main — la pile ne reflète alors
plus la bonne hiérarchie pour personne.

`contextvars.ContextVar` résout exactement ce problème : chaque Task asyncio
reçoit automatiquement sa PROPRE copie du contexte au moment de sa création,
et les `set()`/`reset()` faits dans une Task ne sont jamais visibles par une
Task concurrente. Le comportement séquentiel (cas actuel de la majorité du
code) reste rigoureusement identique ; c'est uniquement le cas concurrent
(aujourd'hui latent, potentiellement actif demain) qui devient correct.

L'API publique (scope, push, pop, to_dict, get, __getattr__) est inchangée :
aucun appelant existant n'a besoin d'être modifié.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Dict, Any, Tuple
import uuid


class ExecutionContext:
    """
    Représente le contexte d'exécution courant.
    Porté par RuntimeState.
    """

    def __init__(self):
        # La pile est un tuple immuable stocké dans une ContextVar : chaque
        # push crée un NOUVEAU tuple (jamais de mutation partagée), et chaque
        # Task/coroutine asyncio voit sa propre vue de cette variable.
        self._stack_var: ContextVar[Tuple[Dict[str, Any], ...]] = ContextVar(
            f"execution_context_stack_{id(self)}", default=()
        )

    @contextmanager
    def scope(self, **kwargs):
        stack = self._stack_var.get()
        old_context = stack[-1].copy() if stack else {}
        new_context = old_context.copy()
        new_context.update(kwargs)

        # Générer un span_id pour ce nouveau contexte (sauf si déjà fourni)
        if "span_id" not in new_context:
            new_context["span_id"] = str(uuid.uuid4())
        # Le parent_span_id est l'ancien span_id au sommet de la pile
        new_context["parent_span_id"] = stack[-1].get("span_id") if stack else None

        token = self._stack_var.set(stack + (new_context,))
        try:
            yield
        finally:
            # reset() restaure exactement la valeur d'avant ce scope(), même
            # si une exception a été levée à l'intérieur du bloc `with`.
            self._stack_var.reset(token)

    def push(self, **kwargs):
        """Version sans context manager (pour les cas où on ne peut pas utiliser 'with').
        À utiliser avec pop() dans le MÊME flux séquentiel (pas à cheval sur deux tasks)."""
        stack = self._stack_var.get()
        old_context = stack[-1].copy() if stack else {}
        new_context = old_context.copy()
        new_context.update(kwargs)
        if "span_id" not in new_context:
            new_context["span_id"] = str(uuid.uuid4())
        new_context["parent_span_id"] = stack[-1].get("span_id") if stack else None
        self._stack_var.set(stack + (new_context,))

    def pop(self):
        """Restaure le contexte précédent."""
        stack = self._stack_var.get()
        if stack:
            self._stack_var.set(stack[:-1])

    def to_dict(self) -> Dict[str, Any]:
        """Retourne une copie du contexte courant (au sommet de la pile)."""
        stack = self._stack_var.get()
        return stack[-1].copy() if stack else {}

    def get(self, key: str, default=None):
        return self.to_dict().get(key, default)

    def __getattr__(self, name):
        val = self.get(name)
        if val is not None:
            return val
        raise AttributeError(f"'{name}' not in context")
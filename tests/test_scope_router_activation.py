"""P5.5 : le routeur de scope doit être activable sans modifier l'hôte.

Avant : `RuntimeState.__init__` posait `enable_scope_router = False` et
**aucun** chemin (config, env, méthode `configure`) ne le relisait. Le flag
était donc mesurable uniquement en éditant le code, ce qui rendait P5.6
impossible.

Convention retenue (déjà utilisée par `MANAGENT_EVENTS_SINK` et
`MANAGENT_RECORD_PROMPTS`) : variable d'environnement. Défaut = off, donc
comportement byte-identique tant que personne ne l'active.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.runtime_state import RuntimeState

ENV = "MANAGENT_SCOPE_ROUTER"


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert RuntimeState().enable_scope_router is False


def test_enabled_by_env(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    assert RuntimeState().enable_scope_router is True


def test_other_values_keep_it_off(monkeypatch):
    for value in ("0", "", "true", "yes", "2"):
        monkeypatch.setenv(ENV, value)
        assert RuntimeState().enable_scope_router is False, value

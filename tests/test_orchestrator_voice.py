"""Voix de l'hôte : Orchestrateur seul, autres entités neutres. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import Llm


def test_orchestrator_llm_receives_host_prompt():
    llm = Llm(provider_manager=None, provider_id="groq", model_id="llama-3.3-70b-versatile",
              system_prompt="Tu es Alfred, majordome.")
    assert llm.system_prompt == "Tu es Alfred, majordome."
    assert llm.clone().system_prompt == "Tu es Alfred, majordome."


def test_other_entities_stay_neutral_by_default():
    llm = Llm(provider_manager=None, provider_id="groq", model_id="llama-3.3-70b-versatile")
    assert llm.system_prompt == ""


def test_orchestrator_wiring_present():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(base, "core", "orchestrator.py"), encoding="utf-8").read()
    assert 'self.llm.system_prompt = getattr(self.runtime_state, "system_prompt", "")' in src

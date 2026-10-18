"""BUG C (P0, version sobre) — convergence finale : critère concret + preuve entière.

1) Le critère (`expected_result`) ne doit plus être le but entier, mais celui de
   la dernière étape RÉELLE (jamais un `direct_answer` qui ne fait que reformuler
   la réponse -> auto-validation).
2) La preuve utile ne doit plus être amputée (evidence capée, world tronqué) :
   c'est la troncature du monde qui masquait l'absence du fichier « apollo ».
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.alignment as alignment
import core.solver as solver_mod
from core.plan_models import ConvergenceDecision
from core.solver import Solver, _pick_final_criteria


def _node(desc, exp, stype="tool_call", status="success"):
    return types.SimpleNamespace(
        description=desc, expected_result=exp, step_type=stype, status=status)


def test_ignore_direct_answer():
    nodes = [
        _node("clic fichier", "true"),
        _node("repond", "le fichier apollo est ouvert", stype="direct_answer"),
    ]
    desc, exp = _pick_final_criteria("but", nodes)
    assert desc == "clic fichier"
    assert exp == "but"


def test_critere_concret_utilise():
    nodes = [_node("verifie vlc", "VLC affiche la video apollo en pause")]
    desc, exp = _pick_final_criteria("but", nodes)
    assert desc == "verifie vlc"
    assert exp == "VLC affiche la video apollo en pause"


def test_repli_sur_le_but_si_aucun_noeud():
    assert _pick_final_criteria("but", []) == ("but", "but")


def test_ignore_noeuds_non_success():
    nodes = [_node("echec", "critere fort", status="failed")]
    assert _pick_final_criteria("but", nodes) == ("but", "but")


class _Capture:
    def __init__(self):
        self.kwargs = None

    def load(self, name, **kwargs):
        self.kwargs = kwargs
        return "PROMPT"


class _FakeLlm:
    def __init__(self):
        pass

    def clone(self, role_name=None):
        return self

    def enable_discovery(self, *a, **k):
        pass

    def set_data_context(self, *a, **k):
        pass

    async def generate_structured(self, **k):
        return ConvergenceDecision(is_convergent=True)


def test_verif_finale_monde_complet_et_critere(monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(solver_mod, "get_prompt_loader", lambda: cap)

    long_world = "MONDE:" + ",".join("w%d" % i for i in range(500))
    assert len(long_world) > 600
    calls = {}

    async def _fake_snap(rs, max_chars=800):
        calls["max_chars"] = max_chars
        return long_world

    monkeypatch.setattr(alignment, "fetch_world_state_snapshot", _fake_snap)

    s = Solver.__new__(Solver)
    s.goal = "but"
    s.id = "m"
    s.depth = 0
    s.variable_registry = {}
    s.executor = types.SimpleNamespace(_perception_trail=[])
    s.runtime_state = types.SimpleNamespace(
        language="en", mission_rum={}, perception_warnings_by_solver={})
    s.current_attempt = types.SimpleNamespace(nodes=[_node("verifie", "VLC apollo en pause")])
    s.llm = _FakeLlm()

    ok, _why = asyncio.run(
        s._verify_mission_convergence(types.SimpleNamespace(response="fait"), strict=False))

    assert ok is True
    assert cap.kwargs["world_snapshot"] == long_world
    assert cap.kwargs["expected_result"] == "VLC apollo en pause"
    assert calls["max_chars"] >= 3000

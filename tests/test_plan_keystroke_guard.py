"""Garde contre les « marches de frappes » aveugles (vague 1, B5).

Run `22a92120` (bureau) : trois frappes d'un caractere d'affilee (`o`, `n`,
`b`) visaient des raccourcis supposes. Aucune n'a ete prouvee par une
observation ; l'une a ouvert un programme et fait deriver la mission.

Regle structurelle, sans liste de touches ni nom d'outil :
- 3 caracteres isoles consecutifs, meme outil clavier, sans observation
  intercalee -> refus deterministe ;
- un caractere isole -> avertissement non bloquant (une saisie d'un caractere
  est legitime).
"""

from types import SimpleNamespace

from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import (
    PlanValidator,
    find_blind_keystroke_runs,
    find_unguarded_keystroke_warnings,
)

_TOOLS = {
    "press": {
        "parameters": {
            "properties": {
                "text": {"type": "string"},
                "mode": {"type": "string"},
                "key": {"type": "string"},
            }
        }
    },
    "observe": {
        "parameters": {"properties": {"question": {"type": "string"}}}
    },
    # Outil qui accepte du texte mais n'expose aucune touche : jamais un
    # clavier, donc hors garde.
    "write_note": {
        "parameters": {"properties": {"text": {"type": "string"}}}
    },
}


class _Manager:
    _tools = _TOOLS


_MANAGER = _Manager()


def _step(sid, tool, args, stype=StepType.TOOL_CALL, **extra):
    return PlanStep(
        id=sid,
        description=sid,
        type=stype,
        tool_name=tool,
        tool_args_json=args if isinstance(args, str) else __import__("json").dumps(args),
        **extra,
    )


def _plan(steps):
    return Plan(goal="tester", steps=steps)


def test_trois_caracteres_consecutifs_refuses():
    plan = _plan([
        _step("s1", "press", {"text": "o"}),
        _step("s2", "press", {"text": "n"}),
        _step("s3", "press", {"text": "b"}),
    ])
    hits = find_blind_keystroke_runs(plan, _MANAGER)
    assert len(hits) == 1
    assert "s3" in hits[0]
    assert "press" in hits[0]


def test_deux_caracteres_ne_suffisent_pas():
    plan = _plan([
        _step("s1", "press", {"text": "o"}),
        _step("s2", "press", {"text": "n"}),
    ])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []


def test_observation_intercalee_autorise():
    plan = _plan([
        _step("s1", "press", {"text": "o"}),
        _step("s2", "observe", {"question": "que voit-on ?"}),
        _step("s3", "press", {"text": "n"}),
    ])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []


def test_saisie_multi_caracteres_autorisee():
    plan = _plan([
        _step("s1", "press", {"text": "bonjour"}),
        _step("s2", "press", {"text": "monde"}),
        _step("s3", "press", {"text": "encore"}),
    ])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []


def test_outil_sans_signature_clavier_hors_garde():
    plan = _plan([
        _step("s1", "write_note", {"text": "a"}),
        _step("s2", "write_note", {"text": "b"}),
        _step("s3", "write_note", {"text": "c"}),
    ])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []


def test_mode_touche_n_est_pas_un_texte_d_un_caractere():
    plan = _plan([
        _step("s1", "press", {"mode": "key", "text": "o"}),
        _step("s2", "press", {"mode": "key", "text": "n"}),
        _step("s3", "press", {"mode": "key", "text": "b"}),
    ])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []


def test_caractere_isole_avertit_sans_bloquer():
    plan = _plan([_step("s1", "press", {"text": "o"})])
    assert find_blind_keystroke_runs(plan, _MANAGER) == []
    warnings = find_unguarded_keystroke_warnings(plan, _MANAGER)
    assert len(warnings) == 1
    assert "s1" in warnings[0]


def test_validate_refuse_la_marche_avant_le_juge():
    class _ForbiddenLlm:
        async def generate_structured(self, *args, **kwargs):
            raise AssertionError("le refus deterministe doit preceler le juge")

    validator = PlanValidator(
        llm=_ForbiddenLlm(),
        prompt_loader=SimpleNamespace(load=lambda *a, **k: ""),
        rules_text="",
        available_tools={"press", "observe", "write_note"},
        tools_manager=_MANAGER,
    )
    plan = _plan([
        _step("s1", "press", {"text": "o"}),
        _step("s2", "press", {"text": "n"}),
        _step("s3", "press", {"text": "b"}),
    ])
    import asyncio

    outcome = asyncio.run(
        validator.validate(plan, child_solver_id="c1", target_goal="trier")
    )
    assert outcome.is_valid is False
    assert "trois frappes" in outcome.reason

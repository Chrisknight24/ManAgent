"""
Tests pour core/plan_validator.py.

Deux volets, comme le module lui-même :
1. detect_repeated_plan_pattern — logique pure, déterministe, sans LLM.
2. validate() — orchestration complète (appel LLM + confirmation humaine
   optionnelle), avec LLM et callback de confirmation tous deux mockés.
"""

import types
import pytest

from core.plan_models import Plan, PlanStep, StepType, PlanValidationDecision, RiskLevel
from core.execution_models import PlanAttempt, ExecutionTree, ExecutionNode
from core.plan_validator import PlanValidator, PlanValidationOutcome


class FakePromptLoader:
    """
    Double de test local — PlanValidator reçoit prompt_loader par injection
    de constructeur et n'importe jamais core.prompt_loader lui-même, donc ce
    test n'a besoin d'aucun stub du vrai module : juste un objet qui expose
    .load(name, lang=..., **kwargs) -> str.
    """

    def __init__(self):
        self.calls = []

    def load(self, template_name, lang="fr", **kwargs):
        self.calls.append({"template_name": template_name, "lang": lang, **kwargs})
        return "[PROMPT:" + template_name + "] " + " | ".join(f"{k}={v!r}" for k, v in kwargs.items())


# =====================================================
# Fixtures / doubles de test
# =====================================================

def make_step(id_, type_=StepType.TOOL_CALL, tool_name="some_tool", is_irreversible=False, **kw):
    defaults = dict(
        id=id_,
        description=f"Étape {id_}",
        type=type_,
        expected_result="true",
        tool_name=tool_name if type_ == StepType.TOOL_CALL else None,
        is_irreversible=is_irreversible,
    )
    defaults.update(kw)
    return PlanStep(**defaults)


def make_plan(goal="faire quelque chose", steps=None):
    return Plan(goal=goal, steps=steps or [make_step("step_1")])


def make_failed_attempt(attempt_number, steps_dicts, outcome="failed"):
    return PlanAttempt(
        attempt_number=attempt_number,
        outcome=outcome,
        proposed_plan={"goal": "g", "steps": steps_dicts},
    )


class ScriptedLlm:
    def __init__(self, response: PlanValidationDecision = None, raise_exc: Exception = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    async def generate_structured(self, prompt, schema, tag=None, **kwargs):
        self.calls.append({"prompt": prompt, "schema": schema, "tag": tag})
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def make_validator(llm=None, rules_text="RULES", confirm_cb=None):
    return PlanValidator(
        llm=llm,
        prompt_loader=FakePromptLoader(),
        rules_text=rules_text,
        language="fr",
        request_human_confirmation=confirm_cb,
    )


# =====================================================
# 1. detect_repeated_plan_pattern
# =====================================================

def test_no_warning_when_no_previous_attempts():
    v = make_validator()
    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    assert v.detect_repeated_plan_pattern(plan, []) is None


def test_no_warning_when_previous_attempt_succeeded():
    v = make_validator()
    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    same_structure = [{"type": "tool_call", "tool_name": "close_app"}]
    prev = [make_failed_attempt(1, same_structure, outcome="success")]
    # Même structure, mais la tentative précédente a RÉUSSI : pas un pattern
    # récursif d'échec, donc pas d'avertissement.
    assert v.detect_repeated_plan_pattern(plan, prev) is None


def test_no_warning_when_structure_differs():
    v = make_validator()
    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    different_structure = [{"type": "tool_call", "tool_name": "open_app"}]
    prev = [make_failed_attempt(1, different_structure)]
    assert v.detect_repeated_plan_pattern(plan, prev) is None


def test_warning_when_identical_structure_previously_failed():
    v = make_validator()
    plan = make_plan(steps=[
        make_step("step_1", tool_name="close_app"),
        make_step("step_2", type_=StepType.DIRECT_ANSWER, tool_name=None),
    ])
    same_structure = [
        {"type": "tool_call", "tool_name": "close_app"},
        {"type": "direct_answer", "tool_name": None},
    ]
    prev = [make_failed_attempt(1, same_structure)]
    warning = v.detect_repeated_plan_pattern(plan, prev)
    assert warning is not None
    assert "1 tentative" in warning


def test_warning_counts_multiple_repeats():
    v = make_validator()
    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    same_structure = [{"type": "tool_call", "tool_name": "close_app"}]
    prev = [
        make_failed_attempt(1, same_structure),
        make_failed_attempt(2, same_structure),
    ]
    warning = v.detect_repeated_plan_pattern(plan, prev)
    assert "2 tentative" in warning


def test_ignores_attempts_without_proposed_plan():
    v = make_validator()
    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    attempt_without_plan = PlanAttempt(attempt_number=1, outcome="failed", proposed_plan=None)
    assert v.detect_repeated_plan_pattern(plan, [attempt_without_plan]) is None


def test_description_differences_do_not_prevent_detection():
    # Le point central : la structure (type+outil) compte, pas le texte.
    v = make_validator()
    plan = make_plan(steps=[
        PlanStep(id="s1", description="Fermer le bloc-notes maintenant",
                  type=StepType.TOOL_CALL, expected_result="true", tool_name="close_app")
    ])
    same_structure_diff_text = [{"type": "tool_call", "tool_name": "close_app"}]
    prev = [make_failed_attempt(1, same_structure_diff_text)]
    assert v.detect_repeated_plan_pattern(plan, prev) is not None


# =====================================================
# 2. validate() — orchestration complète
# =====================================================

async def test_validate_conformant_plan_no_confirmation_needed():
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=True, reason="ok", risk_level=RiskLevel.LOW,
        requires_human_confirmation=False
    ))
    v = make_validator(llm=llm)
    plan = make_plan()

    outcome = await v.validate(plan, "solver-1", target_goal="faire X")

    assert outcome.is_valid is True
    assert bool(outcome) is True
    assert outcome.risk_level == RiskLevel.LOW
    assert outcome.requires_human_confirmation is False
    assert len(llm.calls) == 1
    assert llm.calls[0]["tag"] == "PlanValidationDecision"
    assert llm.calls[0]["schema"] is PlanValidationDecision


async def test_validate_non_conformant_plan_rejected():
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=False, reason="viole la règle 2", risk_level=RiskLevel.MEDIUM
    ))
    v = make_validator(llm=llm)
    plan = make_plan()

    outcome = await v.validate(plan, "solver-1", target_goal="faire X")

    assert outcome.is_valid is False
    assert bool(outcome) is False
    assert outcome.reason == "viole la règle 2"
    # Une confirmation humaine n'a pas de sens sur un plan déjà rejeté.
    assert outcome.requires_human_confirmation is False


async def test_validate_conformant_but_requires_confirmation_approved():
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=True, reason="action critique", risk_level=RiskLevel.CRITICAL,
        requires_human_confirmation=True, irreversibility_flags=["step_1"]
    ))

    confirmations_requested = []

    async def confirm_cb(plan, decision):
        confirmations_requested.append(decision)
        return True

    v = make_validator(llm=llm, confirm_cb=confirm_cb)
    plan = make_plan()

    outcome = await v.validate(plan, "solver-1", target_goal="faire X")

    assert outcome.is_valid is True
    assert outcome.requires_human_confirmation is True
    assert outcome.human_confirmed is True
    assert len(confirmations_requested) == 1
    assert outcome.irreversibility_flags == ["step_1"]


async def test_validate_conformant_but_confirmation_denied():
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=True, reason="action critique", risk_level=RiskLevel.CRITICAL,
        requires_human_confirmation=True
    ))

    async def confirm_cb(plan, decision):
        return False

    v = make_validator(llm=llm, confirm_cb=confirm_cb)
    outcome = await v.validate(make_plan(), "solver-1", target_goal="faire X")

    assert outcome.is_valid is False
    assert outcome.human_confirmed is False
    assert outcome.requires_human_confirmation is True


async def test_validate_requires_confirmation_but_no_channel_fails_safe():
    # Point de conception important : SANS canal de confirmation, on ne
    # laisse JAMAIS passer silencieusement un plan qui en aurait besoin.
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=True, reason="action critique", risk_level=RiskLevel.CRITICAL,
        requires_human_confirmation=True
    ))
    v = make_validator(llm=llm, confirm_cb=None)  # pas de canal

    outcome = await v.validate(make_plan(), "solver-1", target_goal="faire X")

    assert outcome.is_valid is False
    assert outcome.requires_human_confirmation is True
    assert "aucun canal" in outcome.reason.lower()


async def test_validate_confirmation_callback_exception_fails_safe():
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=True, reason="action critique", risk_level=RiskLevel.CRITICAL,
        requires_human_confirmation=True
    ))

    async def broken_confirm_cb(plan, decision):
        raise RuntimeError("frontend indisponible")

    v = make_validator(llm=llm, confirm_cb=broken_confirm_cb)
    outcome = await v.validate(make_plan(), "solver-1", target_goal="faire X")

    assert outcome.is_valid is False
    assert outcome.human_confirmed is False


async def test_validate_llm_exception_fails_safe_as_critical():
    v = make_validator(llm=ScriptedLlm(raise_exc=RuntimeError("LLM down")))
    outcome = await v.validate(make_plan(), "solver-1", target_goal="faire X")

    assert outcome.is_valid is False
    assert outcome.risk_level == RiskLevel.CRITICAL
    assert "LLM down" in outcome.reason


async def test_validate_injects_pattern_warning_into_prompt():
    llm = ScriptedLlm(response=PlanValidationDecision(is_conformant=True, reason="ok"))
    v = make_validator(llm=llm)

    plan = make_plan(steps=[make_step("step_1", tool_name="close_app")])
    same_structure = [{"type": "tool_call", "tool_name": "close_app"}]
    prev = [make_failed_attempt(1, same_structure)]

    await v.validate(plan, "solver-1", target_goal="faire X", previous_attempts=prev)

    prompt = llm.calls[0]["prompt"]
    assert "1 tentative" in prompt  # le pattern_warning est bien passé au template


async def test_validate_injects_declared_irreversible_steps_into_prompt():
    llm = ScriptedLlm(response=PlanValidationDecision(is_conformant=True, reason="ok"))
    v = make_validator(llm=llm)

    plan = make_plan(steps=[make_step("step_1", tool_name="delete_file", is_irreversible=True)])
    await v.validate(plan, "solver-1", target_goal="faire X")

    prompt = llm.calls[0]["prompt"]
    assert "step_1" in prompt


def test_outcome_bool_protocol_matches_is_valid():
    ok = PlanValidationOutcome(is_valid=True, reason="ok")
    ko = PlanValidationOutcome(is_valid=False, reason="ko")
    assert bool(ok) is True
    assert bool(ko) is False
    assert (not ko) is True


# =====================================================
# summarize_mission_history (remplace detect_ancestor_goal_recursion, dont
# l'heuristique de similarité de texte produisait des faux positifs
# documentés sur des décompositions légitimes)
# =====================================================

def _make_node(description, status="success", child_tree=None):
    return ExecutionNode(
        step_id="s1", description=description, step_type="tool_call",
        status=status, child_execution_tree=child_tree,
    )


def test_summary_handles_missing_tree():
    v = make_validator()
    assert "aucun historique" in v.summarize_mission_history(None).lower()


def test_summary_shows_goal_and_status_of_root():
    v = make_validator()
    tree = ExecutionTree(solver_id="root", goal="Créer un jeu Snake", status="running")
    summary = v.summarize_mission_history(tree)
    assert "Créer un jeu Snake" in summary
    assert "running" in summary


def test_summary_shows_failed_attempt_with_reason():
    v = make_validator()
    attempt = PlanAttempt(
        attempt_number=1, outcome="failed",
        failure_reason="L'étape 'step_4' a expected_result='any' sans output_variable_name.",
    )
    tree = ExecutionTree(solver_id="root", goal="Ouvrir le navigateur", status="running", attempts=[attempt])
    summary = v.summarize_mission_history(tree)
    assert "ÉCHEC" in summary
    assert "output_variable_name" in summary


def test_summary_recurses_into_child_execution_tree():
    v = make_validator()
    child = ExecutionTree(solver_id="child", goal="Rechercher sur YouTube", status="success")
    node = _make_node("Ouvrir le navigateur et chercher", child_tree=child)
    attempt = PlanAttempt(attempt_number=1, outcome="success", nodes=[node])
    root = ExecutionTree(solver_id="root", goal="Mission complète", status="running", attempts=[attempt])
    summary = v.summarize_mission_history(root)
    assert "Rechercher sur YouTube" in summary  # le sous-arbre enfant apparaît bien


def test_summary_caps_display_depth_gracefully():
    v = make_validator()
    # Empile 6 niveaux d'imbrication pour dépasser max_display_depth (4 par défaut)
    deepest = ExecutionTree(solver_id="d6", goal="Niveau 6", status="running")
    current = deepest
    for i in range(5, 0, -1):
        node = _make_node(f"Étape niveau {i}", child_tree=current)
        attempt = PlanAttempt(attempt_number=1, outcome="success", nodes=[node])
        current = ExecutionTree(solver_id=f"d{i}", goal=f"Niveau {i}", status="running", attempts=[attempt])
    summary = v.summarize_mission_history(current, max_display_depth=4)
    assert "profondeur d'affichage atteinte" in summary


def test_reproduction_of_reported_false_positive_scenario():
    """
    Reproduction directe du cas signalé comme faux positif : un Solver
    racine dont le plan a UNE étape abstract_task qui ne délègue qu'UNE
    PARTIE de l'objectif global (ouvrir le navigateur + chercher), le reste
    (capture d'écran, lecture de fichier) restant à SON niveau. L'ancienne
    détection par similarité de texte comparait le texte complet de
    l'objectif racine à celui de l'étape déléguée et les trouvait
    "similaires à 82 %", concluant à tort à une récursion dégénérée alors
    qu'aucune tentative n'avait même encore échoué nulle part. Avec
    l'historique de mission (fait, pas heuristique), rien ne doit indiquer
    un échec ici puisqu'il n'y en a aucun dans l'arbre.
    """
    v = make_validator()
    root_goal = (
        "Ouvrir le navigateur, rechercher 'etoo fils' sur YouTube, fermer YouTube, "
        "prendre une capture d'écran sauvegardée sous screen.png sur le bureau, "
        "et analyser le fichier a.csv sur le bureau."
    )
    # Aucune tentative n'a encore été exécutée à ce stade (c'est la toute
    # première proposition de plan) : previous_attempts serait vide, ET
    # l'arbre de mission ne contient encore aucun échec.
    root_tree = ExecutionTree(solver_id="root", goal=root_goal, status="running", attempts=[])
    summary = v.summarize_mission_history(root_tree)
    assert "ÉCHEC" not in summary  # rien n'a échoué, donc rien ne doit ressembler à un signal d'alerte

    # Le plan qui a été refusé à tort dans le cas réel :
    plan = make_plan(
        goal=root_goal,
        steps=[
            make_step("step_1", type_=StepType.ABSTRACT_TASK, tool_name=None),
            make_step("step_2", tool_name="vision"),
            make_step("step_3", tool_name="read_file"),
            make_step("step_4", type_=StepType.DIRECT_ANSWER, tool_name=None),
        ],
    )
    plan.steps[0].description = "Ouvrir le navigateur et effectuer la recherche 'etoo fils' sur YouTube."
    assert plan.steps[0].type == StepType.ABSTRACT_TASK  # sanity check du montage du scénario


async def test_validate_end_to_end_with_real_mission_tree_no_false_positive():
    root_goal = (
        "Ouvrir le navigateur, rechercher 'etoo fils' sur YouTube, fermer YouTube, "
        "prendre une capture d'écran sauvegardée sous screen.png sur le bureau, "
        "et analyser le fichier a.csv sur le bureau."
    )
    root_tree = ExecutionTree(solver_id="root", goal=root_goal, status="running", attempts=[])

    plan = make_plan(
        goal=root_goal,
        steps=[
            make_step("step_1", type_=StepType.ABSTRACT_TASK, tool_name=None),
            make_step("step_2", tool_name="vision"),
        ],
    )
    plan.steps[0].description = "Ouvrir le navigateur et effectuer la recherche 'etoo fils' sur YouTube."

    llm = ScriptedLlm(response=PlanValidationDecision(is_conformant=True, reason="ok"))
    v = make_validator(llm=llm)

    outcome = await v.validate(plan, "root", target_goal=root_goal, mission_history_tree=root_tree)

    assert outcome.is_valid is True  # plus de faux positif : rien dans l'historique ne justifiait un refus
    prompt = llm.calls[0]["prompt"]
    assert "mission_history_summary=" in prompt  # l'historique factuel est bien transmis au juge

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
from core.execution_models import PlanAttempt
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
# detect_ancestor_goal_recursion (nouveau — cas réel : boucle infinie de
# délégation abstract_task -> abstract_task avec le même objectif reformulé)
# =====================================================

def test_no_ancestor_warning_when_chain_empty():
    v = make_validator()
    plan = make_plan(goal="Fermer les fenêtres cibles")
    assert v.detect_ancestor_goal_recursion(plan, None) is None
    assert v.detect_ancestor_goal_recursion(plan, []) is None


def test_no_ancestor_warning_when_goals_are_genuinely_different():
    v = make_validator()
    plan = make_plan(goal="Ouvrir l'explorateur de fichiers dans le dossier Téléchargements")
    ancestor_chain = [
        {"depth": 0, "goal": "Nettoyer le bureau de l'utilisateur", "solver_id": "root"},
        {"depth": 1, "goal": "Identifier les fichiers temporaires à supprimer", "solver_id": "s1"},
    ]
    assert v.detect_ancestor_goal_recursion(plan, ancestor_chain) is None


def test_known_limitation_genuine_paraphrase_not_reliably_caught():
    # LIMITE CONNUE, documentée volontairement par un test plutôt que
    # silencieusement : difflib compare la similarité de CARACTÈRES, pas le
    # sens. Une vraie paraphrase sémantique (mêmes mots-clés, syntaxe très
    # différente) peut passer sous le seuil, contrairement à une répétition
    # quasi-verbatim (le cas réellement observé en production). Une
    # amélioration future pourrait réutiliser le SentenceTransformerProvider
    # déjà présent dans le stack (embeddings, cf. Retriever) pour une
    # similarité sémantique au lieu d'une similarité de caractères — pas
    # fait ici pour rester une dépendance stdlib-only, légère et testable
    # sans mock d'un modèle d'embeddings.
    v = make_validator()
    plan = make_plan(goal="Fermer toutes les applications ouvertes de l'utilisateur")
    ancestor_chain = [
        {"depth": 0, "goal": "Fermer les fenêtres et processus des applications utilisateur cibles identifiées", "solver_id": "root"},
    ]
    warning = v.detect_ancestor_goal_recursion(plan, ancestor_chain)
    # Ce test documente l'état actuel (None = non détecté) — s'il se met à
    # détecter un jour (seuil ajusté, ou passage à une similarité
    # sémantique), il faudra explicitement mettre ce test à jour plutôt que
    # de le laisser échouer par surprise.
    assert warning is None


def test_ancestor_warning_on_near_identical_reformulated_goal():
    # Reproduction directe du cas réel observé en test : chaque niveau
    # reformule légèrement "fermer les applications/fenêtres cibles" sans
    # jamais agir concrètement.
    v = make_validator()
    plan = make_plan(goal="Fermer l'ensemble des fenêtres et processus applicatifs utilisateur cibles")
    ancestor_chain = [
        {"depth": 0, "goal": "Fermer toutes les applications ouvertes de l'utilisateur", "solver_id": "root"},
        {"depth": 1, "goal": "Fermer les fenêtres et processus des applications cibles identifiées", "solver_id": "s1"},
    ]
    warning = v.detect_ancestor_goal_recursion(plan, ancestor_chain)
    assert warning is not None
    assert "récursif" in warning or "ANCÊTRE" in warning


def test_ancestor_warning_triggers_on_abstract_task_step_not_just_plan_goal():
    # Le plan lui-même peut avoir un goal différent (ex: nom de l'étape
    # parente), mais contenir une étape abstract_task qui, elle, reformule
    # PRESQUE À L'IDENTIQUE l'objectif d'un ancêtre — c'est CE pattern
    # précis (quasi-verbatim, pas une vraie paraphrase sémantique) qui a
    # produit la boucle infinie en test réel : difflib attrape bien le
    # quasi-identique, pas une reformulation vraiment différente en surface
    # (voir test_no_ancestor_warning_when_goals_are_genuinely_different et
    # la limite documentée sur detect_ancestor_goal_recursion).
    v = make_validator()
    plan = make_plan(
        goal="Étape de nettoyage du bureau",
        steps=[
            make_step("step_1", type_=StepType.ABSTRACT_TASK, tool_name=None),
        ],
    )
    plan.steps[0].description = "Fermer les fenêtres et processus des applications cibles identifiées"
    ancestor_chain = [
        {"depth": 0, "goal": "Fermer les fenêtres et processus des applications utilisateur cibles identifiées", "solver_id": "root"},
    ]
    warning = v.detect_ancestor_goal_recursion(plan, ancestor_chain)
    assert warning is not None


def test_ancestor_warning_reports_the_best_matching_depth():
    v = make_validator()
    plan = make_plan(goal="Fermer les fenêtres et processus applicatifs cibles")
    ancestor_chain = [
        {"depth": 0, "goal": "Faire le ménage sur l'ordinateur", "solver_id": "root"},  # peu similaire
        {"depth": 3, "goal": "Fermer les fenêtres et processus des applications cibles", "solver_id": "s3"},  # quasi identique
    ]
    warning = v.detect_ancestor_goal_recursion(plan, ancestor_chain)
    assert warning is not None
    assert "profondeur 3" in warning


async def test_validate_end_to_end_rejects_on_ancestor_recursion_signal():
    # Bout-en-bout : même si le LLM (mocké ici) suit l'instruction du prompt
    # de rejeter quand ancestor_warning est présent, on vérifie que le
    # signal est bien PASSÉ AU PROMPT — condition nécessaire pour que le
    # juge puisse s'en servir.
    llm = ScriptedLlm(response=PlanValidationDecision(
        is_conformant=False,
        reason="Ce plan ne fait que redéléguer le même objectif que l'ancêtre à la profondeur 1, sans action concrète nouvelle.",
        risk_level=RiskLevel.LOW,
    ))
    v = make_validator(llm=llm)
    plan = make_plan(goal="Fermer les fenêtres et processus applicatifs cibles")
    ancestor_chain = [
        {"depth": 1, "goal": "Fermer les fenêtres et processus des applications cibles identifiées", "solver_id": "s1"},
    ]

    outcome = await v.validate(plan, "solver-X", target_goal="Nettoyer le bureau", ancestor_chain=ancestor_chain)

    assert outcome.is_valid is False
    prompt = llm.calls[0]["prompt"]
    assert "récursion inter-niveaux" not in prompt  # ce texte est dans le TEMPLATE réel, pas le FakePromptLoader du test
    # Le point qui compte vraiment : le signal calculé est bien remonté jusqu'au prompt.
    assert "ancestor_warning=" in prompt

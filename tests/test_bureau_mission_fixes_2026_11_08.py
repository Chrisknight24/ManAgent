"""Correctifs issus de l'analyse de la mission bureau (2026-11-08).

Chaque cas couvre un comportement AGNOSTIQUE de l'hote (aucun nom d'outil ni
champ computer-use code en dur dans les prompts generiques) :

1. retrieval : bascule vers l'espace vectoriel du modele actif avant requete ;
2. repetition : 1re redite = avertissement, 2e = strict ;
3. collisions de cibles : alias herite reutilisable, doublon intra-plan refuse ;
4. reparation `output_target_name` omis (unique seulement, jamais une devinette) ;
5. faits derives du monde (fenetres reduites / avant-plan) ;
6. re-verification de cible apres selection (`TARGET_MISMATCH`) ;
7. `alignment_failure.observed` porte le texte vu (pas l'image base64) ;
8. un saut de ligne dans `text` est un avertissement, plus un refus dur ;
9. `retry_rationale` est optionnel et transmis au juge.
"""

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import derive_world_facts, render_world_snapshot
from core.execution_models import ExecutionNode, FailureClass, PlanAttempt
from core.plan_models import (
    MissionSignature,
    Plan,
    PlanStep,
    PlanValidationDecision,
    RiskLevel,
    StepType,
)
from core.plan_validator import PlanValidator, find_embedded_control_chars
from core.planner import Planner, repair_missing_target_declarations
from core.retriever import Retriever
from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload
from core.targeting import ObservedWorld, resolve_target
from core.targets import TargetRegistry
from tools import internal_tools as IT
from utils.logger import Logger


# ---------------------------------------------------------------------------
# Helpers communs
# ---------------------------------------------------------------------------


def _tool_step(sid, *, tool="keyboard", args=None, out=None, target=None, desc=None):
    return PlanStep(
        id=sid,
        description=desc or f"etape {sid}",
        type=StepType.TOOL_CALL,
        tool_name=tool,
        tool_args_json=json.dumps(args or {}),
        output_target_name=target,
    )


def _attempt(num, plan, *, outcome="failed", nodes=None):
    executed = nodes if nodes is not None else [
        ExecutionNode(
            step_id="step_1", description="d", step_type="tool_call", status="success"
        )
    ]
    return PlanAttempt(
        attempt_number=num,
        outcome=outcome,
        proposed_plan=plan.model_dump(mode="json"),
        failure_class=(
            FailureClass.EXECUTION_FAILURE if outcome == "failed" else FailureClass.NONE
        ),
        failure_reason="boom" if outcome == "failed" else "",
        nodes=executed,
    )


def _contract(raw, tool_name="scan"):
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


def _scan_contract(*, disclosure=None):
    raw = {
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {"ref": "id", "description": "name", "attributes": {"role": "role"}},
        }],
    }
    if disclosure is not None:
        raw["disclosure"] = disclosure
    return _contract(raw)


class _FakeManager:
    def __init__(self, contracts=None, payloads=None):
        self.contracts = contracts or {}
        self.payloads = payloads or {}
        self.calls = []

    def get_target_contract(self, name):
        return self.contracts.get(name)

    def get_tool_returns(self, name):
        return []

    async def register_typed_assets(self, assets, session_id="default_session"):
        return {asset.target_id: asset.target_id for asset in assets}

    async def execute_tool(self, name, args):
        self.calls.append((name, dict(args)))
        result = self.payloads.get(name, {"result": True, "data": {}})
        if callable(result):
            result = result(dict(args))
        return json.dumps(result)


class _ScriptedLlm:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []
        self.tags = []

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.prompts.append(prompt)
        self.tags.append(tag)
        decision = self.decisions.pop(0) if self.decisions else {"no_target": True}
        if callable(decision):
            decision = decision(prompt)
        if isinstance(decision, dict):
            return schema(**decision)
        return decision


# ---------------------------------------------------------------------------
# 1. Retrieval : interroger l'espace vectoriel du modele actif
# ---------------------------------------------------------------------------


class _FakeProfileStore:
    def __init__(self):
        self.models = []
        self.queries = []

    def use_embedding_model(self, model):
        self.models.append(model)

    def get_similar_profiles(self, **kwargs):
        self.queries.append(kwargs)
        return []


class _FakeCache:
    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        return None


class _FakeEmbedManager:
    def __init__(self, model):
        self.active_provider = types.SimpleNamespace(model_name=model)
        self.active_provider_id = "fake-provider"

    async def embed(self, text):
        return [0.0, 1.0, 0.0]


def test_retrieval_bascule_sur_l_espace_du_modele_actif():
    store = _FakeProfileStore()
    manager = _FakeEmbedManager("hf/paraphrase-multilingual")
    retriever = Retriever.__new__(Retriever)
    retriever.top_k = 3
    retriever.threshold = 0.5
    retriever.profile_store = store
    retriever.mission_store = types.SimpleNamespace()
    retriever.cache_manager = _FakeCache()
    retriever.skill_registry = None
    retriever.runtime_state = types.SimpleNamespace(
        active_embedding_model="legacy/model",
        current_mission_id="mission-1",
        embedding_manager=manager,
    )

    asyncio.run(retriever.retrieve([MissionSignature(action="sort", object="desktop")]))

    # Le store est bascule AVANT la requete, sur `provider.model_name` exact.
    assert store.models == ["hf/paraphrase-multilingual"]
    assert store.queries, "aucune requete emise"
    assert store.queries[0]["embedding_model"] == "hf/paraphrase-multilingual"


# ---------------------------------------------------------------------------
# 2. Echelle de repetition : 1 = avertissement, 2 = strict
# ---------------------------------------------------------------------------


def _two_step_plan():
    return Plan(goal="g", steps=[
        _tool_step("step_1", tool="keyboard"),
        _tool_step("step_2", tool="mouse"),
    ])


def test_repetition_un_echec_est_un_avertissement():
    plan = _two_step_plan()
    fact = PlanValidator.build_repetition_fact(plan, [_attempt(1, plan)])
    assert fact is not None
    assert "1re répétition" in fact
    assert "non bloquant" in fact


def test_repetition_deux_echecs_devient_stricte():
    plan = _two_step_plan()
    fact = PlanValidator.build_repetition_fact(
        plan, [_attempt(1, plan), _attempt(2, plan)]
    )
    assert fact is not None
    assert "2 échecs" in fact
    assert "confirmée" in fact


def test_motif_repetition_distingue_un_et_deux_echecs():
    validator = PlanValidator.__new__(PlanValidator)
    plan = _two_step_plan()

    one = validator.detect_repeated_plan_pattern(plan, [_attempt(1, plan)])
    assert one is not None and "1re RÉPÉTITION" in one
    assert "non bloquant" in one

    two = validator.detect_repeated_plan_pattern(
        plan, [_attempt(1, plan), _attempt(2, plan)]
    )
    assert two is not None and "RÉPÉTITION CONFIRMÉE" in two


def test_repetition_ignore_les_rejets_avant_execution():
    plan = _two_step_plan()
    fact = PlanValidator.build_repetition_fact(
        plan, [_attempt(1, plan, outcome="rejected", nodes=[])]
    )
    assert fact is not None
    assert "rejet(s) AVANT exécution" in fact
    assert "échec À L'EXÉCUTION" not in fact


# ---------------------------------------------------------------------------
# 3. Collisions de cibles : alias herite vs doublon intra-plan
# ---------------------------------------------------------------------------


def test_alias_de_cible_herite_du_registre_est_autorise():
    plan = Plan(goal="g", steps=[
        _tool_step("step_3", tool="perceive_understand",
                   args={"question": "q", "target_producer": "scan"},
                   target="zone_bureau"),
    ])
    ok, errors = Planner._validate_plan(
        None, plan, {"zone_bureau": {"value": "ancienne cible"}}
    )
    assert ok, errors


def test_alias_de_cible_duplique_dans_le_meme_plan_est_refuse():
    plan = Plan(goal="g", steps=[
        _tool_step("step_3", tool="perceive_understand",
                   args={"question": "q1", "target_producer": "scan"},
                   target="zone_bureau"),
        _tool_step("step_4", tool="perceive_understand",
                   args={"question": "q2", "target_producer": "scan"},
                   target="zone_bureau"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert not ok
    assert any("collision" in e and "CE plan" in e for e in errors)


# ---------------------------------------------------------------------------
# 4. Reparation `output_target_name` omis
# ---------------------------------------------------------------------------


def test_reparation_target_omis_quand_ancrage_unique():
    plan = Plan(goal="g", steps=[
        _tool_step(
            "step_8",
            tool="perceive_understand",
            args={"question": "q", "target_producer": "scan",
                  "target_description": "option Vider la Corbeille"},
        ),
        _tool_step(
            "step_9", tool="mouse",
            args={"action": "click", "target": "$@_option_vider"},
        ),
    ])
    warnings = repair_missing_target_declarations(plan)
    assert plan.steps[0].output_target_name == "option_vider"
    assert warnings and "option_vider" in warnings[0]


def test_pas_de_reparation_quand_ancrage_ambigu():
    plan = Plan(goal="g", steps=[
        _tool_step(
            "step_7", tool="perceive_understand",
            args={"question": "q1", "target_producer": "scan",
                  "target_description": "zone"},
        ),
        _tool_step(
            "step_8", tool="perceive_understand",
            args={"question": "q2", "target_producer": "scan",
                  "target_description": "option"},
        ),
        _tool_step(
            "step_9", tool="mouse",
            args={"action": "click", "target": "$@_option_vider"},
        ),
    ])
    warnings = repair_missing_target_declarations(plan)
    assert warnings == []
    assert plan.steps[0].output_target_name is None
    assert plan.steps[1].output_target_name is None


# ---------------------------------------------------------------------------
# 5. Faits derives du monde
# ---------------------------------------------------------------------------


def test_faits_derives_fenetres_reduites_et_avant_plan():
    facts = derive_world_facts({
        "windows": [
            {"title": "Explorateur", "class": "CabinetWClass", "minimized": True},
            {"title": "Bloc-notes", "class": "Notepad", "minimized": True},
        ],
        "foreground_class": "WorkerW",
    })
    assert any("réduites" in f for f in facts)
    # Toutes reduites : aucune classe non reduite, donc pas de second fait.
    assert not any("n'appartient" in f for f in facts)


def test_faits_derives_avant_plan_hors_des_fenetres_listees():
    facts = derive_world_facts({
        "windows": [
            {"title": "Bloc-notes", "class": "Notepad", "minimized": False},
        ],
        "foreground_class": "WorkerW",
    })
    assert any("n'appartient" in f for f in facts)


def test_faits_derives_absents_sans_structure_exploitable():
    assert derive_world_facts({"summary_text": "Bureau visible"}) == []
    assert derive_world_facts("texte libre") == []


def test_render_snapshot_inclut_les_faits_derives_si_le_budget_le_permet():
    text = render_world_snapshot({
        "summary_text": "Avant-plan : WorkerW",
        "windows": [{"title": "Bloc-notes", "class": "Notepad", "minimized": True}],
        "foreground_class": "WorkerW",
    }, limit=2000)
    assert "Faits dérivés" in text
    assert "réduites" in text


def test_faits_derives_survivent_a_l_omission_des_fenetres():
    """Cas réel : le budget (600) omet `windows` mais garde le fait dérivé."""
    data = {
        "summary_text": "Avant-plan : WorkerW ; focus : Bureau 1.",
        "foreground_title": "",
        "focused": {"name": "Bureau 1", "rect": [0, 0, 1366, 768], "role": "Pane"},
        "foreground_class": "WorkerW",
        "foreground_ours": False,
        "windows": [
            {"title": f"Fichier {i} - Explorateur", "class": "CabinetWClass",
             "minimized": True, "rect": [0, 0, 800, 600]}
            for i in range(8)
        ],
    }
    text = render_world_snapshot(data, 600)
    assert "champs omis" in text          # `windows` n'est pas dans le JSON rendu
    assert "Faits dérivés" in text        # mais le fait est présent
    assert "réduites" in text


# ---------------------------------------------------------------------------
# 6. Re-verification de cible apres selection
# ---------------------------------------------------------------------------


def _world_with_one_candidate():
    contract = _scan_contract()
    registry = TargetRegistry()
    initial = project_target_payload(registry, contract, {
        "result": True,
        "data": {"candidates": [
            {"id": "ref-file", "name": "bonhour.txt", "role": "file"},
        ]},
    })[0]
    world = ObservedWorld.from_payload(registry, contract, initial)
    return contract, registry, world, world.candidates[0].key


def test_verification_rejette_une_cible_de_mauvaise_nature():
    contract, registry, world, key = _world_with_one_candidate()
    manager = _FakeManager(contracts={"scan": contract})
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "plus proche"},
        {"matches": False, "confidence": "high",
         "reason": "c'est un fichier, pas une zone vide"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="un emplacement vide sans icone",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=0,
        verify=True,
    ))
    assert not result.ok
    assert result.error_code == "TARGET_MISMATCH"
    assert result.kind == "none"
    assert len(llm.prompts) == 2  # selection + verification


def test_verification_accepte_une_cible_conforme():
    contract, registry, world, key = _world_with_one_candidate()
    manager = _FakeManager(contracts={"scan": contract})
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "le fichier demande"},
        {"matches": True, "confidence": "high", "reason": "c'est bien le fichier"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le fichier bonhour.txt",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=0,
        verify=True,
    ))
    assert result.ok, result.error_message
    assert result.kind == "local"


def test_verification_fail_open_si_confiance_faible():
    contract, registry, world, key = _world_with_one_candidate()
    manager = _FakeManager(contracts={"scan": contract})
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "candidat"},
        {"matches": False, "confidence": "low", "reason": "incertain"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="un emplacement vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=0,
        verify=True,
    ))
    assert result.ok, result.error_message


def test_verification_desactivee_par_defaut_aucun_appel_supplementaire():
    contract, registry, world, key = _world_with_one_candidate()
    manager = _FakeManager(contracts={"scan": contract})
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "candidat"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le fichier bonhour.txt",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=0,
    ))
    assert result.ok, result.error_message
    assert len(llm.prompts) == 1


# ---------------------------------------------------------------------------
# 7. `alignment_failure.observed` = texte vu, jamais l'image
# ---------------------------------------------------------------------------


def _capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(
        Logger, "event",
        staticmethod(lambda event_type, **fields: events.append(
            {"event": event_type, **fields})),
    )
    return events


def test_alignment_failure_prefere_le_texte_observe(monkeypatch):
    events = _capture_events(monkeypatch)
    IT._emit_alignment_failure(
        None,
        tool="perceive_understand",
        question="Trouve un emplacement vide",
        source="vision",
        out={
            "result": False,
            "message": "Aucune icone a cet endroit, seulement le fichier bonhour.txt",
            "data": {"grid": {"cols": 12}, "image_base64": "/9j/AAAA"},
        },
    )
    assert events
    observed = events[0]["observed"]
    assert "bonhour.txt" in observed
    assert "image_base64" not in observed


def test_alignment_failure_prefere_le_champ_observed(monkeypatch):
    events = _capture_events(monkeypatch)
    IT._emit_alignment_failure(
        None,
        tool="perceive_understand",
        question="q",
        source="vision",
        out={
            "result": False,
            "observed": "Le bureau affiche deux icones : a.txt et b.txt",
            "message": "Analyse négative",
            "data": {"image_base64": "/9j/AAAA", "grid": {}},
        },
    )
    observed = events[0]["observed"]
    assert observed.startswith("Le bureau affiche deux icones")
    assert "image_base64" not in observed


# ---------------------------------------------------------------------------
# 8. Caractere de controle : avertissement, plus un refus dur
# ---------------------------------------------------------------------------


class _Loader:
    def load(self, name, **kwargs):
        return "prompt de test"


class _ConformantJudge:
    async def generate_structured(self, prompt, schema, tag, **kwargs):
        assert schema is PlanValidationDecision
        return PlanValidationDecision(
            is_conformant=True, reason="ok", risk_level=RiskLevel.LOW
        )


class _ToolsForText:
    _tools = {
        "keyboard": {
            "parameters": {
                "properties": {
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                }
            }
        }
    }


def test_find_embedded_control_chars_detecte_la_frappe_multiligne():
    plan = Plan(goal="g", steps=[
        _tool_step("step_1", tool="keyboard",
                   args={"mode": "text", "text": "powershell\n"}),
    ])
    hits = find_embedded_control_chars(plan, _ToolsForText())
    assert hits and "step_1" in hits[0]


def test_saut_de_ligne_dans_text_est_un_avertissement_pas_un_refus():
    plan = Plan(goal="g", steps=[
        _tool_step("step_1", tool="keyboard",
                   args={"mode": "text", "text": "powershell -Command \"Clear-RecycleBin -Force\"\n"}),
    ])
    validator = PlanValidator(
        llm=_ConformantJudge(),
        prompt_loader=_Loader(),
        rules_text="",
        language="fr",
        available_tools={"keyboard"},
        tools_manager=_ToolsForText(),
    )
    outcome = asyncio.run(validator.validate(plan, "solver-1", "vider la corbeille"))
    assert outcome.is_valid, outcome.reason
    assert any("argument 'text' contient" in w for w in outcome.warnings)


# ---------------------------------------------------------------------------
# 9. `retry_rationale` : optionnel, transmis au juge
# ---------------------------------------------------------------------------


def test_retry_rationale_est_optionnel():
    assert Plan(goal="g").retry_rationale is None
    plan = Plan(goal="g", retry_rationale="l'etat du monde a change")
    assert plan.retry_rationale == "l'etat du monde a change"


def test_prompts_exposent_la_justification_de_reprise():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("base", "fr", "en"):
        text = open(
            os.path.join(root, "prompts", lang, "plan_validation.md"),
            encoding="utf-8",
        ).read()
        assert "{{ retry_rationale }}" in text, lang

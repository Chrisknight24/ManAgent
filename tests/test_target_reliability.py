"""Fiabilité des cibles (2026-11-09) — contrat, preuves, verdict, zoom.

Aucun nom d'outil ni de champ d'une application n'est codé en dur : tout vient
du manifeste (`disclosure.reliability`, `disclosure.evidence`,
`disclosure.zoom_probe`) et le vérificateur n'élit jamais une cible.
"""

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.target_contract import (
    DEFAULT_EVIDENCE,
    DEFAULT_RELIABILITY,
    parse_target_contract,
)
from core.target_ingress import project_target_payload
from core.targeting import (
    MediaRef,
    ObservedWorld,
    build_target_evidence,
    effective_reliability,
    resolve_target,
)
from core.targets import TargetRegistry
from tools import internal_tools as IT


# ---------------------------------------------------------------------------
# Helpers (aucune dépendance à un hôte réel)
# ---------------------------------------------------------------------------


def _contract(raw, tool_name="scan"):
    result = parse_target_contract(raw, tool_name=tool_name)
    assert result.contract is not None, result.errors
    return result.contract


def _raw_contract(*, disclosure=None):
    raw = {
        "version": 1,
        "consumes": [
            {"param": "target", "scope": "ui_preset", "required": True}
        ],
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "ui_preset",
            "fields": {
                "ref": "id",
                "description": "name",
                "attributes": {"role": "role"},
            },
        }],
    }
    if disclosure is not None:
        raw["disclosure"] = disclosure
    return raw


def _zoom_disclosure(*, reliability="low", preserves_references=False):
    probe = {
        "id": "zoomer",
        "kind": "zoom",
        "description": "Inspecte une candidate locale.",
        "tool": "zoom",
        "subject": {"kind": "local", "param": "target"},
        "produces": ["candidates"],
    }
    if preserves_references:
        probe["preserves_references"] = True
    return {
        "version": 1,
        "reliability": reliability,
        "evidence": ["metadata"],
        "probes": [probe],
        "zoom_probe": "zoomer",
    }


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


def _world(*, contract, candidates):
    registry = TargetRegistry()
    initial = project_target_payload(
        registry, contract, {"result": True, "data": {"candidates": candidates}}
    )[0]
    world = ObservedWorld.from_payload(registry, contract, initial)
    return registry, world


def _inner_key_from_prompt(prompt: str) -> str:
    """Extrait la clé locale de la cible interne listée dans le prompt."""
    candidates = _candidates_from_prompt(prompt)
    for candidate in candidates:
        if candidate.get("description") == "bouton ok":
            return candidate["key"]
    raise AssertionError("cible interne absente du prompt")


def _candidates_from_prompt(prompt: str) -> list:
    block = prompt.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(block)


# ---------------------------------------------------------------------------
# 1. Contrat : profil, preuves, zoom
# ---------------------------------------------------------------------------


def test_disclosure_parses_reliability_evidence_and_zoom_probe():
    contract = _contract(_raw_contract(disclosure=_zoom_disclosure()))
    disclosure = contract.disclosure
    assert disclosure is not None
    assert disclosure.reliability == "low"
    assert disclosure.evidence == ("metadata",)
    assert disclosure.zoom_probe == "zoomer"
    probe = disclosure.probe("zoomer")
    assert probe is not None
    # Absent = faux (comportement historique : nouveau monde seul).
    assert probe.preserves_references is False


def test_disclosure_preserves_references_optionnel():
    contract = _contract(
        _raw_contract(disclosure=_zoom_disclosure(preserves_references=True))
    )
    assert contract.disclosure is not None
    assert contract.disclosure.probe("zoomer").preserves_references is True


def test_disclosure_absent_keeps_historical_behavior():
    contract = _contract(_raw_contract())
    assert contract.disclosure is None
    assert DEFAULT_RELIABILITY == "medium"
    assert DEFAULT_EVIDENCE == ("metadata",)


def test_disclosure_zoom_probe_inconnu_est_ignore():
    disclosure = _zoom_disclosure()
    disclosure["zoom_probe"] = "absente"
    contract = _contract(_raw_contract(disclosure=disclosure))
    assert contract.disclosure is not None
    assert contract.disclosure.zoom_probe is None


def test_surcharges_de_fiabilite_producteur_et_ancre():
    raw = _raw_contract(disclosure={
        "version": 1,
        "reliability": "low",
        "anchors": [
            {"kind": "cell", "param": "cell", "reliability": "high"},
        ],
    })
    raw["produces"][0]["reliability"] = "medium"
    contract = _contract(raw)
    assert effective_reliability(contract.disclosure, contract, "ui_preset") == "medium"
    assert effective_reliability(contract.disclosure, contract, "cell") == "high"
    assert effective_reliability(contract.disclosure, contract, "autre") == "low"


def test_effective_reliability_sans_contrat_reste_medium():
    assert effective_reliability(None, None, "ui_preset") == "medium"


# ---------------------------------------------------------------------------
# 2. Bundle de preuves : uniquement ce que l'hôte déclare
# ---------------------------------------------------------------------------


def _fake_entry(attributes=None, key="local_1"):
    return types.SimpleNamespace(
        local_name=key,
        description="candidat",
        scope="ui_preset",
        attributes=attributes or {"role": "file"},
    )


def test_preuves_par_defaut_metadonnees_seulement():
    contract = _contract(_raw_contract())
    world = ObservedWorld()
    evidence = build_target_evidence(_fake_entry(), world, contract.disclosure)
    assert set(evidence) == {"description", "scope", "attributes"}


def test_preuves_declarees_image_rect_nested_invariants():
    disclosure = _zoom_disclosure()
    disclosure["evidence"] = ["metadata", "image", "rect", "nested_targets", "invariants"]
    contract = _contract(_raw_contract(disclosure=disclosure))
    registry, world = _world(
        contract=contract,
        candidates=[
            {"id": "ref-1", "name": "candidat", "role": "file"},
            {"id": "ref-2", "name": "interne", "role": "icon"},
        ],
    )
    world.media = [MediaRef(uri="asset-1", mime="image/png", name="crop")]
    entry = registry.get(world.candidates[0].key)
    entry.attributes = {
        "role": "file",
        "rect": [1, 2, 3, 4],
        "invariants": {"visible": True},
    }
    evidence = build_target_evidence(entry, world, contract.disclosure)
    assert evidence["rect"] == [1, 2, 3, 4]
    assert evidence["invariants"] == {"visible": True}
    assert evidence["image"][0]["uri"] == "asset-1"
    assert len(evidence["nested"]) == 1
    assert evidence["nested"][0]["key"] != entry.local_name


# ---------------------------------------------------------------------------
# 3. Verdict : uncertain accepté + warning, mismatch refuse ou zoome
# ---------------------------------------------------------------------------


def _one_candidate_contract():
    return _contract(_raw_contract())


def test_uncertain_accepte_avec_warning_partage():
    contract = _one_candidate_contract()
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    key = world.candidates[0].key
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "candidat"},
        {"verdict": "uncertain", "confidence": "low", "reason": "preuve faible"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_FakeManager(contracts={"scan": contract}),
        allow_anchor=False,
        max_rounds=0,
        verify=True,
    ))
    assert result.ok, result.error_message
    assert result.warnings
    assert "réservation" in result.warnings[0].lower() or "reserve" in result.warnings[0].lower()
    assert result.verification is not None
    assert result.verification["verdict"] == "uncertain"


def test_mismatch_sans_zoom_est_refuse():
    contract = _one_candidate_contract()
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    key = world.candidates[0].key
    llm = _ScriptedLlm([
        {"target_key": key, "target_kind": "local", "reason": "candidat"},
        {"verdict": "mismatch", "confidence": "high", "reason": "mauvaise nature"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=_FakeManager(contracts={"scan": contract}),
        allow_anchor=False,
        max_rounds=0,
        verify=True,
    ))
    assert not result.ok
    assert result.error_code == "TARGET_MISMATCH"
    assert result.verification is not None


def test_mismatch_zoome_ancienne_candidate_puis_selection_interne():
    contract = _contract(_raw_contract(disclosure=_zoom_disclosure()))
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    old_key = world.candidates[0].key
    zoom_payload = {
        "result": True,
        "data": {"candidates": [
            {"id": "ref-inner", "name": "bouton ok", "role": "button"},
        ]},
    }
    manager = _FakeManager(
        contracts={"scan": contract}, payloads={"zoom": zoom_payload}
    )
    llm = _ScriptedLlm([
        {"target_key": old_key, "target_kind": "local", "reason": "candidat"},
        {
            "verdict": "mismatch",
            "confidence": "high",
            "next": "zoom",
            "prefer_internal": "local_inner",
            "reason": "le fichier contient un bouton interne",
        },
        lambda prompt: {
            "target_key": _inner_key_from_prompt(prompt),
            "target_kind": "local",
            "reason": "cible interne",
        },
        {"verdict": "match", "confidence": "high", "reason": "bonne cible"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le bouton ok",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=1,
        verify=True,
    ))
    assert result.ok, result.error_message
    assert result.entry is not None
    assert result.entry.description == "bouton ok"
    # La sonde a bien visé l'ANCIENNE candidate locale (sa référence brute,
    # car l'outil de zoom n'a pas de consommateur déclaré ici).
    assert manager.calls == [
        ("zoom", {"target": registry.get(old_key).ref.value})
    ]
    # Le sélecteur a été réinterrogé avec l'indice du vérificateur.
    assert len(llm.prompts) == 4
    assert "Indice du vérificateur" in llm.prompts[2]
    assert "bouton interne" in llm.prompts[2]
    assert "local_inner" in llm.prompts[2]
    # Politique de zoom (option B) : la sonde a réussi et produit une
    # candidate, donc le nouveau monde fait foi. L'ancienne n'est PAS
    # ré-ajoutée : sa référence peut avoir été renumérotée par l'hôte.
    second_candidates = _candidates_from_prompt(llm.prompts[2])
    assert old_key not in {c["key"] for c in second_candidates}
    assert "bouton ok" in {c["description"] for c in second_candidates}
    # Les deux verdicts sont conservés (mismatch initial inclus).
    assert [v["verdict"] for v in result.verifications] == ["mismatch", "match"]
    assert all(v.get("local_name") for v in result.verifications)
    assert result.verification["verdict"] == "match"
    assert len(llm.tags) == 4


def test_zoom_epuise_replanifie_sans_boucle():
    contract = _contract(_raw_contract(disclosure=_zoom_disclosure()))
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    old_key = world.candidates[0].key
    manager = _FakeManager(
        contracts={"scan": contract},
        payloads={"zoom": {"result": True, "data": {"candidates": []}}},
    )
    llm = _ScriptedLlm([
        {"target_key": old_key, "target_kind": "local", "reason": "candidat"},
        {"verdict": "mismatch", "confidence": "high", "next": "zoom",
         "reason": "mauvaise nature"},
        {"no_target": True},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone vide",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=1,
        verify=True,
    ))
    assert not result.ok
    # Une seule sonde de zoom, jamais de boucle.
    assert [call[0] for call in manager.calls] == ["zoom"]
    # Sonde vide -> ancien monde conservé : l'ancienne candidate reste
    # visible dans la re-sélection (prompt 0 = sélection, 1 = vérification,
    # 2 = re-sélection).
    second_candidates = _candidates_from_prompt(llm.prompts[2])
    assert old_key in {c["key"] for c in second_candidates}
    # Le verdict mismatch initial reste tracé.
    assert [v["verdict"] for v in result.verifications] == ["mismatch"]


def test_zoom_preservant_les_references_garde_ancienne_candidate():
    contract = _contract(_raw_contract(
        disclosure=_zoom_disclosure(preserves_references=True)
    ))
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    old_key = world.candidates[0].key
    zoom_payload = {
        "result": True,
        "data": {"candidates": [
            {"id": "ref-inner", "name": "bouton ok", "role": "button"},
        ]},
    }
    manager = _FakeManager(
        contracts={"scan": contract}, payloads={"zoom": zoom_payload}
    )
    llm = _ScriptedLlm([
        {"target_key": old_key, "target_kind": "local", "reason": "candidat"},
        {"verdict": "mismatch", "confidence": "high", "next": "zoom",
         "reason": "le fichier contient un bouton interne"},
        lambda prompt: {
            "target_key": _inner_key_from_prompt(prompt),
            "target_kind": "local",
            "reason": "cible interne",
        },
        {"verdict": "match", "confidence": "high", "reason": "bonne cible"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le bouton ok",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=1,
        verify=True,
    ))
    assert result.ok, result.error_message
    # La sonde garantit les anciennes références : les deux mondes coexistent.
    second_candidates = _candidates_from_prompt(llm.prompts[2])
    assert old_key in {c["key"] for c in second_candidates}
    assert "bouton ok" in {c["description"] for c in second_candidates}


def test_zoom_en_echec_conserve_ancien_monde_et_confirme_ancienne():
    contract = _contract(_raw_contract(disclosure=_zoom_disclosure()))
    registry, world = _world(
        contract=contract,
        candidates=[{"id": "ref-file", "name": "bonjour.txt", "role": "file"}],
    )
    old_key = world.candidates[0].key
    manager = _FakeManager(
        contracts={"scan": contract},
        payloads={"zoom": {
            "result": False,
            "error_reason": "TARGET_STALE_REF",
            "data": {"candidates": []},
        }},
    )
    llm = _ScriptedLlm([
        {"target_key": old_key, "target_kind": "local", "reason": "candidat"},
        {"verdict": "mismatch", "confidence": "high", "next": "zoom",
         "reason": "doute sur la nature"},
        # La sonde ayant échoué, l'ancienne référence reste valide : le
        # sélecteur peut la reconfirmer.
        {"target_key": old_key, "target_kind": "local", "reason": "confirmee"},
        {"verdict": "match", "confidence": "high", "reason": "bonne cible"},
    ])
    result = asyncio.run(resolve_target(
        llm=llm,
        question="le fichier bonjour.txt",
        world=world,
        contract=contract,
        registry=registry,
        tools_mgr=manager,
        allow_anchor=False,
        max_rounds=1,
        verify=True,
    ))
    assert result.ok, result.error_message
    assert result.entry is not None
    assert result.entry.description == "bonjour.txt"
    # Une seule sonde, et l'ancien monde est resté visible après l'échec.
    assert [call[0] for call in manager.calls] == ["zoom"]
    second_candidates = _candidates_from_prompt(llm.prompts[2])
    assert old_key in {c["key"] for c in second_candidates}
    assert [v["verdict"] for v in result.verifications] == ["mismatch", "match"]


# ---------------------------------------------------------------------------
# 4. Canal de warnings partagé + événement
# ---------------------------------------------------------------------------


def test_store_target_warnings_range_et_trace(monkeypatch):
    captured = []
    monkeypatch.setattr(IT.Logger, "event", lambda name, **fields: captured.append((name, fields)))
    runtime_state = types.SimpleNamespace(
        execution_context={"solver_id": "solver-1", "mission_id": "mission-1"}
    )
    result = types.SimpleNamespace(
        warnings=["Cible acceptée sous réserve : preuve faible"],
        verification={
            "verdict": "uncertain",
            "confidence": "low",
            "next": "accept",
            "reliability": "low",
            "reason": "preuve faible",
        },
        entry=types.SimpleNamespace(local_name="local_1"),
    )
    IT._store_target_warnings(runtime_state, result, source="perceive_understand")
    assert runtime_state.target_warnings_by_solver["solver-1"] == [
        "Cible acceptée sous réserve : preuve faible"
    ]
    assert captured[0][0] == "target.verification"
    assert captured[0][1]["verdict"] == "uncertain"
    assert captured[0][1]["local_name"] == "local_1"
    assert captured[0][1]["solver_id"] == "solver-1"


def test_store_target_warnings_emet_un_evenement_par_verdict(monkeypatch):
    captured = []
    monkeypatch.setattr(
        IT.Logger, "event", lambda name, **fields: captured.append((name, fields))
    )
    runtime_state = types.SimpleNamespace(
        execution_context={"solver_id": "s", "mission_id": "m"}
    )
    result = types.SimpleNamespace(
        warnings=["Cible acceptée sous réserve : preuve faible"],
        verification={"verdict": "match", "local_name": "local_b"},
        verifications=[
            {
                "verdict": "mismatch",
                "confidence": "high",
                "next": "zoom",
                "reliability": "low",
                "local_name": "local_a",
            },
            {
                "verdict": "match",
                "confidence": "high",
                "next": "accept",
                "reliability": "low",
                "local_name": "local_b",
            },
        ],
        entry=types.SimpleNamespace(local_name="local_b"),
    )
    IT._store_target_warnings(runtime_state, result, source="test")
    # Un événement par verdict, dans l'ordre de la boucle.
    assert [event[1]["verdict"] for event in captured] == ["mismatch", "match"]
    assert [event[1]["local_name"] for event in captured] == ["local_a", "local_b"]
    assert captured[0][1]["attempt"] == 1
    assert captured[0][1]["attempts"] == 2
    assert captured[1][1]["attempt"] == 2
    # Les warnings globaux ne sont rattachés qu'au dernier verdict.
    assert "warnings" not in captured[0][1]
    assert captured[1][1]["warnings"] == [
        "Cible acceptée sous réserve : preuve faible"
    ]


def test_store_target_warnings_dedup_and_cap(monkeypatch):
    monkeypatch.setattr(IT.Logger, "event", lambda name, **fields: None)
    runtime_state = types.SimpleNamespace(execution_context={"solver_id": "s"})
    for i in range(12):
        IT._store_target_warnings(
            runtime_state,
            types.SimpleNamespace(warnings=[f"w{i}", f"w{i}"]),
            source="test",
        )
    assert len(runtime_state.target_warnings_by_solver["s"]) == 10
    assert runtime_state.target_warnings_by_solver["s"][-1] == "w11"

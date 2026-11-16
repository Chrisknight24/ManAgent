"""Analyse de rejeu : la lecture doit rester honnete et deterministe.

Ces bancs figent le contrat de `tools/analyze_replay.py` : il ne juge jamais la
justesse metier, il dit seulement si une cible a ete liee sans preuve positive.
"""

import json

from tools import analyze_replay as A


def _event(name, **fields):
    record = {"ts": "2026-11-14T10:00:00+00:00", "event": name}
    record.update(fields)
    return record


def _write(tmp_path, *events, name="events.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )
    return str(path)


def _base(**extra):
    payload = {
        "mission_id": "m1",
        "solver_id": "s1",
        "attempt_number": 1,
        "step_id": "step_4",
    }
    payload.update(extra)
    return payload


def test_lien_sans_gate_est_signale(tmp_path):
    path = _write(
        tmp_path,
        _event("target.bound", **_base(local_name="T1", alias="cible")),
    )
    events = A.read_events([path])
    attempts = [A.read_attempt(item) for item in A.split_attempts(events)]
    assert attempts[0]["flags"] == ["GATE ABSENT"]
    assert A.main([path, "--strict"]) == 1


def test_refus_propre_ne_declenche_aucune_alerte(tmp_path):
    path = _write(
        tmp_path,
        _event(
            "target.gate",
            **_base(status="contradicted", verdict="mismatch", reason="mauvaise nature"),
        ),
    )
    events = A.read_events([path])
    attempts = [A.read_attempt(item) for item in A.split_attempts(events)]
    assert attempts[0]["flags"] == ["REFUS PROPRE"]
    assert A.main([path, "--strict"]) == 0


def test_preuve_puis_liaison_est_acceptee(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="proven", verdict="match")),
        _event("target.bound", **_base(local_name="T1", alias="cible")),
    )
    events = A.read_events([path])
    attempts = [A.read_attempt(item) for item in A.split_attempts(events)]
    assert attempts[0]["flags"] == []
    assert "preuve positive" in A.render_text(attempts[0])
    assert A.main([path, "--strict"]) == 0


def test_doute_sur_une_cible_liee_est_signale(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="unproven", verdict="uncertain")),
        _event("target.bound", **_base(local_name="T1", alias="cible")),
    )
    events = A.read_events([path])
    attempts = [A.read_attempt(item) for item in A.split_attempts(events)]
    assert "LIEN SANS PREUVE" in attempts[0]["flags"]


def test_preuve_tronquee_est_signalee(tmp_path):
    path = _write(
        tmp_path,
        _event(
            "target.gate",
            **_base(
                status="unproven",
                verdict="uncertain",
                reason="fait incomplet (tronque)",
            ),
        ),
    )
    events = A.read_events([path])
    attempts = [A.read_attempt(item) for item in A.split_attempts(events)]
    assert "PREUVE TRONQUEE ?" in attempts[0]["flags"]


def test_lignes_illisibles_ignorees(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            "pas du json",
            "{cassé",
            json.dumps(_event("target.gate", **_base(status="proven", verdict="match"))),
        ]),
        encoding="utf-8",
    )
    events = A.read_events([str(path)])
    assert len(events) == 1
    assert A.main([str(path)]) == 0


def test_json_machine_contient_le_resume(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="proven", verdict="match")),
        _event("target.bound", **_base(local_name="T1", alias="cible")),
    )
    payload = json.loads(_capture(lambda: A.main([path, "--json"])))
    assert payload["summary"]["gates"] == {"proven": 1}
    assert payload["summary"]["verdicts"] == {"match": 1}
    assert payload["attempts"][0]["steps"][0]["bounds"]


def test_filtre_par_mission(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="proven", verdict="match")),
        _event(
            "target.gate",
            **_base(status="unproven", verdict="uncertain", mission_id="m2"),
        ),
    )
    payload = json.loads(
        _capture(lambda: A.main([path, "--json", "--mission-id", "m2"]))
    )
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["mission_id"] == "m2"
    assert payload["summary"]["verdicts"] == {"uncertain": 1}


# ---------------------------------------------------------------------------
# Vitalite : agregats, alertes, integration
# ---------------------------------------------------------------------------


def _call(**fields):
    return _event("llm_call", **fields)


def test_vitalite_agrege_les_appels():
    events = [
        _call(duration_ms=1000, first_byte_ms=200, success=True),
        _call(duration_ms=3000, first_byte_ms=600, success=True),
        _call(duration_ms=2000, first_byte_ms=400, success=True),
    ]
    vitality = A.read_vitality(events)
    assert vitality["calls"] == 3
    assert vitality["failed"] == 0
    assert vitality["duration_ms"] == {"max": 3000, "median": 2000}
    assert vitality["first_byte_ms"] == {
        "count": 3,
        "min": 200,
        "median": 400,
        "max": 600,
    }
    assert vitality["flags"] == []


def test_vitalite_alerte_quand_aucun_premier_contenu_n_est_mesure():
    vitality = A.read_vitality([_call(duration_ms=9000, success=True)])
    assert vitality["first_byte_ms"]["count"] == 0
    assert vitality["first_byte_ms"]["median"] is None
    assert "AUCUN FIRST BYTE" in vitality["flags"]


def test_vitalite_alerte_sur_echec_et_battement():
    events = [
        _call(duration_ms=1000, first_byte_ms=100, success=False),
        _event("llm_call.stall", kind="structured", tick=1, elapsed_s=30, budget_s=120),
        _event("llm_call.stall", kind="structured", tick=2, elapsed_s=60, budget_s=120),
    ]
    vitality = A.read_vitality(events)
    assert vitality["failed"] == 1
    assert vitality["stalls"] == {"count": 2, "max_elapsed_s": 60}
    assert "APPELS EN ECHEC" in vitality["flags"]


def test_vitalite_lit_le_budget_du_monde():
    events = [
        _event("world_snapshot", chars=1800, budget=2000),
        _event("world_snapshot", chars=2400, budget=2000, truncated=True),
        _event("world_snapshot.budget_low", budget=800),
    ]
    vitality = A.read_vitality(events)
    assert vitality["world"] == {
        "snapshots": 2,
        "chars_min": 1800,
        "chars_max": 2400,
        "budgets": [2000],
        "budget_low": 1,
    }
    assert "BUDGET BAS" in vitality["flags"]
    assert "SNAPSHOT COURT ?" not in vitality["flags"]


def test_vitalite_signale_un_snapshot_court():
    vitality = A.read_vitality([_event("world_snapshot", chars=900, budget=900)])
    assert "SNAPSHOT COURT ?" in vitality["flags"]


def test_vitalite_compare_premier_contenu_et_duree_du_meme_appel():
    events = [
        _call(duration_ms=5000, first_byte_ms=300, success=True),
        _call(duration_ms=300, first_byte_ms=900, success=True),
    ]
    vitality = A.read_vitality(events)
    # Seul le second appel est incoherent ; le premier (300 < 5000) ne l'est pas.
    assert "MESURE INCOHERENTE" in vitality["flags"]


def test_vitalite_ne_prend_pas_un_booleen_pour_une_mesure():
    vitality = A.read_vitality([_call(duration_ms=True, first_byte_ms=False)])
    assert vitality["duration_ms"]["max"] is None
    assert vitality["first_byte_ms"]["count"] == 0
    # Sans premiere mesure reelle, l'alerte reste due.
    assert "AUCUN FIRST BYTE" in vitality["flags"]


def test_vitalite_vide_est_honnete():
    vitality = A.read_vitality([])
    assert vitality["calls"] == 0
    assert vitality["duration_ms"] == {"max": None, "median": None}
    assert vitality["first_byte_ms"] == {
        "count": 0,
        "min": None,
        "median": None,
        "max": None,
    }
    assert vitality["stalls"] == {"count": 0, "max_elapsed_s": None}
    assert vitality["world"]["snapshots"] == 0
    assert vitality["flags"] == []


def test_render_vitalite_dit_ce_qui_manque():
    text = A.render_vitality(A.read_vitality([_call(duration_ms=1200, success=True)]))
    assert "appels          : 1" in text
    assert "aucun premier contenu mesure" in text
    assert "AUCUN FIRST BYTE" in text


def test_render_vitalite_lisible_quand_tout_va_bien():
    events = [
        _call(duration_ms=1500, first_byte_ms=250, success=True),
        _event("world_snapshot", chars=1900, budget=2000),
        _event("llm_call.stall", kind="structured", tick=1, elapsed_s=30, budget_s=120),
    ]
    text = A.render_vitality(A.read_vitality(events))
    assert "1 mesure(s), min=250 ms median=250 ms max=250 ms" in text
    assert "1 battement(s), max 30 s" in text
    assert "chars 1900..1900" in text
    assert "alertes         : aucune" in text


def test_main_texte_ajoute_la_vitalite(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="proven", verdict="match")),
        _call(duration_ms=900, first_byte_ms=120, success=True, **_base()),
    )
    text = _capture(lambda: A.main([path]))
    assert "vitalite" in text
    assert "1 mesure(s), min=120 ms" in text


def test_json_machine_contient_la_vitalite(tmp_path):
    path = _write(
        tmp_path,
        _event("target.gate", **_base(status="proven", verdict="match")),
        _call(duration_ms=900, first_byte_ms=120, success=True, **_base()),
    )
    payload = json.loads(_capture(lambda: A.main([path, "--json"])))
    assert payload["vitality"]["calls"] == 1
    assert payload["vitality"]["first_byte_ms"]["median"] == 120


def test_run_sans_tentative_mais_avec_appels_reste_analysable(tmp_path):
    path = _write(
        tmp_path,
        _call(duration_ms=800, first_byte_ms=90, success=True, **_base()),
    )
    assert A.main([path]) == 0
    payload = json.loads(_capture(lambda: A.main([path, "--json"])))
    assert payload["attempts"] == []
    assert payload["vitality"]["calls"] == 1


def test_filtre_par_mission_s_applique_a_la_vitalite(tmp_path):
    path = _write(
        tmp_path,
        _call(duration_ms=800, first_byte_ms=90, success=True, **_base()),
        _call(
            duration_ms=5000,
            first_byte_ms=4800,
            success=True,
            **_base(mission_id="m2"),
        ),
    )
    payload = json.loads(
        _capture(lambda: A.main([path, "--json", "--mission-id", "m2"]))
    )
    assert payload["vitality"]["calls"] == 1
    assert payload["vitality"]["duration_ms"]["max"] == 5000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(call):
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = call()
    assert code in (0, 1, 2)
    return buffer.getvalue()

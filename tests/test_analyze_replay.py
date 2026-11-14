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

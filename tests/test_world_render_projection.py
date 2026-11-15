"""Vue PLANIFICATEUR du monde : des cibles, pas des coordonnees.

Vecu (run dcc9ec4b) : le planner voyait `foreground_class: "WorkerW"`,
`rect: [0,0,1366,768]` et un focus nomme « Bureau 1 » — de quoi vouloir
cliquer sur des pixels, et rien pour agir proprement.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import (
    PLANNER_HIDDEN_FIELDS,
    project_world_for_planner,
    render_world_snapshot,
)
from core.plan_models import Plan, PlanStep, StepType
from core.plan_validator import find_unresolved_plan_refs

# Etat du monde tel que l'hote l'a reellement envoye (extrait du run).
_WORLD = {
    "summary_text": "Avant-plan actif ; focus : Bureau 1.",
    "foreground_title": "",
    "focused": {
        "name": "Bureau 1",
        "rect": [0, 0, 1366, 768],
        "role": "Pane",
        "class": "WorkerW",
        "automationId": "42",
        "pid": 2548,
        "target": "$@_desktop",
    },
    "windows": [
        {
            "title": "CondiFlow - Qt Creator",
            "state": "background",
            "rect": [10, 20, 800, 600],
            "class": "Qt5152QWindowIcon",
            "target": "$@_window_3",
        }
    ],
}


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def test_projection_retire_geometrie_et_bruit():
    out = render_world_snapshot(_WORLD, limit=4000, projection="planner")
    for interdit in ("rect", "1366", "2548", "automationId", "WorkerW", "Qt5152QWindowIcon"):
        assert interdit not in out, f"{interdit!r} ne doit pas atteindre le planner"


def test_la_prose_de_l_hote_n_est_pas_reécrite():
    """Le résumé est une phrase de l'hôte : on ne la réécrit pas, on la garde."""
    out = render_world_snapshot(
        {"summary_text": "Avant-plan : WorkerW ; focus : Bureau 1."},
        limit=400,
        projection="planner",
    )
    assert "Avant-plan : WorkerW" in out


def test_projection_garde_le_sens():
    out = render_world_snapshot(_WORLD, limit=4000, projection="planner")
    assert "Bureau 1" in out
    assert "CondiFlow - Qt Creator" in out
    assert "$@_desktop" in out


def test_projection_hisse_la_cible_en_tete():
    projected = project_world_for_planner({"name": "Bureau 1", "target": "$@_desktop"})
    assert list(projected)[0] == "target"


def test_vue_raw_intacte():
    raw = render_world_snapshot(_WORLD, limit=4000)
    assert "rect" in raw
    assert "1366" in raw
    assert "WorkerW" in raw


def test_projection_recursive():
    projected = project_world_for_planner(
        {"a": [{"rect": [0, 0, 1, 1], "keep": "oui", "x": 3}]}
    )
    assert projected == {"a": [{"keep": "oui"}]}


def test_projection_ne_leve_jamais():
    assert project_world_for_planner(None) is None
    assert project_world_for_planner("texte") == "texte"
    assert project_world_for_planner({1: {"rect": []}}) == {1: {}}


# --------------------------------------------------------------------------
# Troncature : les champs omis sont NOMMES
# --------------------------------------------------------------------------

def test_champs_omis_nommes():
    data = {"summary_text": "ok", "windows": [{"title": "a" * 200}]}
    out = render_world_snapshot(data, limit=120, projection="planner")
    assert "champs omis" in out
    assert "windows" in out


def test_aucun_nom_de_champ_invente_dans_le_marqueur():
    data = {"summary_text": "ok", "focus": {"name": "x"}, "windows": [{"title": "a" * 200}]}
    out = render_world_snapshot(data, limit=140, projection="planner")
    if "champs omis" in out:
        assert "windows" in out


# --------------------------------------------------------------------------
# B2 : la cible du monde s'utilise TELLE QUELLE
# --------------------------------------------------------------------------

def test_target_du_monde_accepte_par_le_validateur():
    projected = project_world_for_planner(_WORLD)
    noms = [
        entry["target"]
        for entry in [projected["focused"]] + projected["windows"]
        if isinstance(entry, dict) and entry.get("target")
    ]
    assert noms

    plan = Plan(
        goal="g",
        steps=[
            PlanStep(
                id="step_1",
                description="clic droit sur le bureau",
                type=StepType.TOOL_CALL,
                tool_name="click",
                tool_args_json=json.dumps({"target": "$@_desktop"}),
                expected_result="true",
            )
        ],
    )
    assert find_unresolved_plan_refs(plan, extra_names=noms) == []
    assert find_unresolved_plan_refs(plan) != []


def test_liste_des_champs_caches_est_stable():
    """Contrat lisible : la liste est explicite, pas une heuristique cachee."""
    assert "rect" in PLANNER_HIDDEN_FIELDS
    assert "class" in PLANNER_HIDDEN_FIELDS
    assert "automationid" in PLANNER_HIDDEN_FIELDS
    assert "target" not in PLANNER_HIDDEN_FIELDS
    assert "label" not in PLANNER_HIDDEN_FIELDS

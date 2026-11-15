"""Vue PLANIFICATEUR du monde : de la nature, pas des coordonnees.

Vecu (run dcc9ec4b) : le bloc monde envoye au planificateur ne contenait que
294 caracteres de bruit mecanique — `focused: {"name": "", "rect": [...],
"role": "ComboBox"}`, `foreground_class`, `foreground_ours` — et une liste de
fenetres entiere qui ne tenait pas, remplacee par « (+1 champs omis) ». Rien
pour decider, et de quoi vouloir cliquer sur des pixels.

`_WORLD_RUN` reproduit la STRUCTURE reelle de cette charge utile. Les libelles
sont anonymises : aucun nom d'application hote n'entre dans ce depot.
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

# Structure de la charge utile reellement recue (libelles anonymises) :
# un resume, un focus avec geometrie et classe, et des fenetres. Aucun champ
# `target` : l'hote n'en publiait pas. C'est le fait mesure.
_WORLD_RUN = {
    "summary_text": "Avant-plan : [main] - Editeur ; focus : ComboBox.",
    "foreground_title": "[main] - Editeur",
    "focused": {
        "name": "",
        "rect": [39, 47, 146, 28],
        "role": "ComboBox",
    },
    "foreground_class": "ClasseNoyau",
    "foreground_ours": False,
    "windows": [
        {
            "title": "Document sans titre",
            "state": "background",
            "rect": [10, 20, 800, 600],
            "class": "ClasseFenetre",
        }
    ],
}

# Charge utile SYNTHETIQUE : elle sert a figer la regle « un handle du monde
# s'utilise tel quel ». Ce n'est pas une observation du run.
_WORLD_HANDLES = {
    "summary_text": "Avant-plan actif ; focus : Bureau 1.",
    "focused": {"name": "Bureau 1", "role": "Pane", "target": "$@_desktop"},
    "windows": [{"title": "Document", "target": "$@_window_3"}],
}


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def test_projection_retire_geometrie_et_bruit():
    out = render_world_snapshot(_WORLD_RUN, limit=4000, projection="planner")
    for interdit in ("rect", "39", "146", "ClasseNoyau", "ClasseFenetre"):
        assert interdit not in out, f"{interdit!r} ne doit pas atteindre le planner"


def test_la_prose_de_l_hote_n_est_pas_reécrite():
    """Le résumé est une phrase de l'hôte : on ne la réécrit pas, on la garde."""
    out = render_world_snapshot(
        {"summary_text": "Avant-plan : ClasseNoyau ; focus : Bureau 1."},
        limit=400,
        projection="planner",
    )
    assert "Avant-plan : ClasseNoyau" in out


def test_projection_garde_le_sens():
    out = render_world_snapshot(_WORLD_RUN, limit=4000, projection="planner")
    assert "ComboBox" in out
    assert "[main] - Editeur" in out
    assert "Document sans titre" in out


def test_projection_garde_les_handles_du_monde():
    out = render_world_snapshot(_WORLD_HANDLES, limit=4000, projection="planner")
    assert "$@_desktop" in out
    assert "$@_window_3" in out


def test_projection_hisse_la_cible_en_tete():
    projected = project_world_for_planner({"name": "Bureau 1", "target": "$@_desktop"})
    assert list(projected)[0] == "target"


def test_vue_raw_intacte():
    raw = render_world_snapshot(_WORLD_RUN, limit=4000)
    assert "rect" in raw
    assert "39" in raw
    assert "ClasseNoyau" in raw


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
# Regle des champs composes : `foreground_class` est du bruit comme `class`
# --------------------------------------------------------------------------

def test_champs_composes_de_bruit_caches():
    data = {
        "foreground_class": "ClasseNoyau",
        "foreground_title": "Document sans titre",
        "window_rect": [0, 0, 10, 10],
        "process_id": 4242,
        "client_handle": "0x1",
        "classification_hint": "garde-moi",
    }
    out = render_world_snapshot(data, limit=4000, projection="planner")
    assert "ClasseNoyau" not in out
    assert "4242" not in out
    assert "0x1" not in out
    assert "Document sans titre" in out
    # Un mot qui CONTIENT un segment ne doit pas etre cache par accident.
    assert "garde-moi" in out


def test_champs_semantiques_jamais_caches():
    data = {
        "name": "Bureau 1",
        "label": "Bureau",
        "role": "Pane",
        "title": "Document",
        "target": "$@_bureau",
        "state": "background",
        "enumerable": False,
    }
    projected = project_world_for_planner(data)
    assert set(projected) == set(data)


# --------------------------------------------------------------------------
# `well_known` : des lieux, pas des cibles
# --------------------------------------------------------------------------

def test_well_known_ne_montre_que_le_libelle():
    data = {
        "well_known": [
            {"name": "bureau", "label": "Bureau (espace de travail)"},
            {"name": "barre", "label": "Barre des tâches"},
            {"name": "orphelin"},
        ]
    }
    out = render_world_snapshot(data, limit=4000, projection="planner")
    assert "Bureau (espace de travail)" in out
    assert "Barre des tâches" in out
    # Un nom qui n'est pas une cible ne doit pas ressembler à une cible.
    assert "$@_" not in out
    assert '"bureau"' not in out


def test_well_known_reste_intact_en_vue_raw():
    data = {"well_known": [{"name": "bureau", "label": "Bureau"}]}
    raw = render_world_snapshot(data, limit=4000)
    assert '"bureau"' in raw


# --------------------------------------------------------------------------
# B2 : la cible du monde s'utilise TELLE QUELLE
# --------------------------------------------------------------------------

def test_target_du_monde_accepte_par_le_validateur():
    projected = project_world_for_planner(_WORLD_HANDLES)
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

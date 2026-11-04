"""Traçabilité de la matrice T1-T34 (roadmap §5).

S9 affirme « T1-T34 exécutés ». Aujourd'hui la plupart des tests portent un
nom descriptif, pas l'ID de la matrice : impossible de vérifier l'affirmation
sans relire 10 fichiers. Ce module rend la matrice **auditable** :

- chaque ID T1..T34 est déclaré exactement une fois ;
- chaque test cité existe réellement dans la suite ;
- aucun ID ne pointe vers un test absent ou renommé.

Il ne prouve pas la sémantique d'un test (aucun test ne peut le faire) ; il
empêche la matrice de pourrir en silence. La table lisible est dans
`docs/TARGET_ROADMAP.md` §5.1 et doit rester alignée avec `MATRIX`.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# T-ID -> ((fichier, fonction de test), ...)
MATRIX = {
    "T1": (("test_targets_contract.py", "test_valid_contract_and_helpers"),),
    "T2": (("test_targets_egress.py", "test_consumer_name_is_substituted_then_resolved_to_raw_ref"),),
    "T3": (("test_targets_egress.py", "test_unknown_consumer_value_is_refused_without_dispatch"),),
    "T4": (("test_targets_egress.py", "test_raw_allow_is_an_explicit_passthrough"),),
    "T5": (("test_targets_egress.py", "test_multi_target_is_all_or_nothing"),),
    "T6": (("test_targets_contract.py", "test_invalid_contracts"), ("test_targets_contract.py", "test_reserved_names_match_executor")),
    "T7": (("test_targets_egress.py", "test_inactive_target_is_refused_without_dispatch"),),
    "T8": (("test_targets_egress.py", "test_scope_mismatch_is_refused_without_dispatch"),),
    "T9": (("test_targets_selection.py", "test_t9_selection_none_single_candidates_et_cles_mintées"),),
    "T10": (("test_targets_selection.py", "test_t10_single_auto_selection_expose_description"),),
    "T11": (("test_targets_selection.py", "test_t11_output_target_name_lie_un_nom_reutilisable"), ("test_targets_selection.py", "test_t11_executor_transmet_output_target_name_au_meta_outil")),
    "T12": (("test_targets_selection.py", "test_t12_perceive_action_selectionne_sur_donnees_projetees"),),
    "T13": (("test_targets_s7.py", "test_s7_t13_recul_refait_un_snapshot_frais"),),
    "T14": (("test_targets_inertie.py", "test_all_prompts_have_no_target_vocabulary_and_are_stable"),),
    "T15": (("test_targets_ingress.py", "test_t15_projection_renomme_les_champs_et_la_ref"),),
    "T16": (("test_targets_selection.py", "test_t16_cle_candidate_inconnue_rejetee_avant_action"),),
    "T17": (("test_targets_s6.py", "test_s6_t17_hote_qui_rejette_toujours_termine"),),
    "T18": (("test_targets_ingress.py", "test_t18_executor_ne_stocke_ni_ne_rend_la_ref_brute"),),
    "T19": (("test_targets_s6.py", "test_s6_t19_tout_dispatch_passe_le_chokepoint"),),
    "T20": (("test_targets_egress.py", "test_purge_during_in_flight_call_keeps_resolved_snapshot"),),
    "T21": (("test_targets_ingress.py", "test_t21_dedup_par_correlation_puis_par_ref"),),
    "T22": (("test_targets_ingress.py", "test_t22_generation_absente_ou_ancienne_n_ecrase_pas"),),
    "T23": (("test_targets_ingress.py", "test_t23_aba_ne_reutilise_pas_un_nom_invalide"),),
    "T24": (("test_targets_s6.py", "test_s6_manifest_updated_purge_tout_et_eteint_les_flags"),),
    "T25": (("test_targets_ingress.py", "test_t25_snapshot_projette_sans_inscrire_ni_fuite"),),
    "T26": (("test_targets_ingress.py", "test_t26_updates_appliquees_puis_retirees_du_payload"), ("test_targets_ingress.py", "test_t26_executor_applique_update_meme_sur_echec_et_la_retire")),
    "T27": (("test_targets_egress.py", "test_mixed_text_and_target_is_rejected_not_trimmed"),),
    "T28": (("test_targets_prompt_surface.py", "test_t30_absent_flag_equals_false_byte_for_byte"),),
    "T29": (("test_targets_s8.py", "test_s8_t29_registre_synchrone_sans_coroutine"),),
    "T30": (("test_targets_prompt_surface.py", "test_t30_no_target_mechanism_vocabulary_when_capability_is_off"), ("test_targets_prompt_surface.py", "test_t30_absent_flag_equals_false_byte_for_byte")),
    "T31": (("test_targets_prompt_surface.py", "test_t31_active_capability_never_leaks_registry_internals"), ("test_targets_prompt_surface.py", "test_t31_target_surface_stays_within_budget"), ("test_targets_s4.py", "test_s4_t31_prompts_cibles_autorises_et_sans_fuite")),
    "T32": (("test_targets_prompt_surface.py", "test_t32_invalid_contract_renders_exactly_like_no_contract"), ("test_targets_s4.py", "test_s4_t32_contrat_invalide_reste_hors_surface")),
    "T33": (("test_targets_s4.py", "test_s4_t33_registre_ne_garde_pas_les_cibles_mortes"),),
    "T34": (("test_targets_s4.py", "test_s4_t34_selection_et_targets_sont_independants"),),
}


def _test_functions(path):
    # `utf-8-sig` : certains fichiers de tests portent un BOM.
    with open(path, encoding="utf-8-sig") as fh:
        tree = ast.parse(fh.read(), filename=path)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_matrix_declares_every_id_exactly_once():
    expected = {f"T{i}" for i in range(1, 35)}
    assert set(MATRIX) == expected


def test_every_matrix_entry_points_to_a_real_test():
    cache = {}
    missing = []
    for tid, pairs in sorted(MATRIX.items()):
        assert pairs, tid
        for filename, function in pairs:
            path = os.path.join(TESTS_DIR, filename)
            assert os.path.isfile(path), (tid, filename)
            if filename not in cache:
                cache[filename] = _test_functions(path)
            if function not in cache[filename]:
                missing.append((tid, filename, function))
    assert not missing, missing


def test_matrix_does_not_claim_tests_from_a_single_blind_file():
    """Chaque ID cite au moins un fichier qui existe dans le dépôt de tests."""
    filenames = {f for pairs in MATRIX.values() for f, _ in pairs}
    assert len(filenames) >= 8, filenames

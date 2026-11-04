"""T30 / T31 / T32 — contrat de surface des prompts (roadmap §3.3).

Ces tests complètent `test_targets_inertie.py` et `test_targets_s4.py` :

- T30 : sans capacité déclarée, **aucun** prompt de **aucune** entité ne
  contient de vocabulaire propre au mécanisme cible (champs legacy,
  grille, ruban, zoom, glisser). C'est le trou qui a laissé passer les
  règles L3 du 28/10 : le scan existant ne cherchait que les 5 jetons du
  registre (`output_target_name`, `TARGET_`, ...).
- T30bis : `targets_enabled` absent == `False`, à l'octet près (règle
  « absent = off » de la roadmap §3.3 règle 2).
- T31 : capacité active → aucun jeton interdit (ref hôte, corrélation,
  génération) et budget de surface respecté, mesuré.
- T32 : contrat invalide → flags off → rendu identique au rendu sans
  contrat, sur **toutes** les entités (pas seulement le Planner).

Aucun nom d'hôte, d'application ou d'outil n'est autorisé ici : les
jetons scannés décrivent le mécanisme, pas un produit.
"""

import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_capabilities import target_prompt_flags
from core.prompt_loader import get_prompt_loader
from tools.tools_manager import ToolsManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_BASE = os.path.join(PROJECT_ROOT, "prompts", "base")
LANGS = ("base", "en", "fr")

# Vocabulaire du **mécanisme cible** : uniquement pertinent quand un
# `target_contract` est actif. Un hôte sans contrat ne doit jamais le voir.
TARGET_MECHANISM_VOCAB = (
    "cell",
    "uia",
    "grid",
    "grille",
    "ribbon",
    "ruban",
    "zoom",
    "drag",
    "glisser",
)

# Extras par entité : verbes d'action UI qui n'ont de sens que dans un
# exemple de cible. Volontairement limité à ces fichiers, car `click`
# apparaît aussi comme verbe générique ailleurs (orchestrator,
# signature_extractor) — préexistant, hors périmètre cible.
#
# Volontairement EXCLUS du scan : `screen`/`écran`, `window`/`fenêtre`,
# `current_screen`. Ce vocabulaire appartient au bloc monde, dont le défaut
# est permissif (`world_guidance_visible`, DETTE-6) : le scanner ici
# reviendrait à corriger DETTE-6 par la bande. Chantier séparé.
TARGET_VOCAB_BY_ENTITY = {
    "planner.md": ("click", "clic"),
    "feasibility.md": ("click", "clic"),
    "plan_grammar.md": ("click", "clic"),
}

# Jetons interdits même quand la capacité est active : ils exposent
# l'intérieur du registre (ref hôte, corrélation, compteur de génération).
# `generation` n'est cherché que sous sa forme de champ JSON : le mot
# « generation » apparaît légitimement ailleurs (ex. classes d'échec).
FORBIDDEN_EVEN_WHEN_ON = (
    "ref-",
    "correlation_id",
    "target_contract",
    '"generation"',
)

_MECHANISM_RE = re.compile(
    r"\b(" + "|".join(TARGET_MECHANISM_VOCAB) + r")\b", re.IGNORECASE
)

# Budget de surface cible, en tokens estimés (1 token ≈ 4 chars), mesuré
# sur le bloc propre de chaque prompt (inclusions déduites).
# §3.3 règle 5 vise ~200 tokens ; l'exemple travaillé de la chaîne cible
# (F5) rend 200 irréaliste sur le Planner. Déviation mesurée et
# documentée dans TARGET_ROADMAP §3.3 : plafond Planner 350.
BUDGET_TOKENS = {
    "planner.md": 320,
    "feasibility.md": 150,
    "plan_grammar.md": 150,
}

CONTRACT = {
    "version": 1,
    "consumes": [{"param": "target", "scope": "ui_element"}],
    "produces": [
        {
            "path": "data.targets",
            "mode": "candidates",
            "scope": "ui_element",
            "fields": {"ref": "id", "description": "label"},
        }
    ],
}

SCHEMA = {
    "type": "object",
    "properties": {"target": {"type": "string"}},
    "required": ["target"],
}


def _manager(target_contract=None):
    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        name="click",
        role="r",
        description="d",
        parameters_schema=SCHEMA,
        target_contract=target_contract,
    )
    return tm


def _flags(tm):
    return target_prompt_flags(types.SimpleNamespace(tools_manager=tm), [])


def _context():
    return {
        "goal": "g",
        "strategy": "s",
        "context": "c",
        "advice": "",
        "variable_registry": {},
        "tools": [],
        "skills": "",
        "model_id": "m",
        "supported_modalities": [],
        "unsupported_modalities": [],
        "similar_missions": "",
        "tools_guidance": "",
        "registry": "",
        "plan_summary": "s",
        "rules": "r",
        "pattern_warning": None,
        "novelty_assessment": "",
        "repetition_fact": "",
        "direct_perception_note": "",
        "mission_history_summary": "",
        "declared_irreversible_steps": [],
        "hitl_policy": "balanced",
        "hitl_policy_text": "",
        "human_validation_history": "",
        "availability_summary": "",
        "user_message": "u",
        "history": "",
        "world_snapshot": "",
        "world_warnings": [],
        "known_facts": "",
        "session_mission_list": [],
        "known_signatures": [],
        "technical_goals": [],
        "data_types_info": {},
        "old_meta_plan": {},
        "trees": [],
        "targets": [],
        "world_guidance": True,
    }


def _all_prompts():
    return sorted(
        name for name in os.listdir(PROMPTS_BASE) if name.endswith(".md")
    )


def _render(name, lang, **overrides):
    ctx = _context()
    ctx.update(overrides)
    return get_prompt_loader().load(name, lang=lang, **ctx)


def test_t30_no_target_mechanism_vocabulary_when_capability_is_off():
    """Sans contrat, aucun prompt ne parle grille / ruban / zoom / glisser."""
    for name in _all_prompts():
        extras = TARGET_VOCAB_BY_ENTITY.get(name, ())
        extra_re = re.compile(
            r"\b(" + "|".join(extras) + r")\b", re.IGNORECASE
        ) if extras else None
        for lang in LANGS:
            out = _render(name, lang, targets_enabled=False,
                          target_selection_enabled=False)
            hit = _MECHANISM_RE.search(out)
            assert hit is None, (name, lang, hit.group(0) if hit else "")
            if extra_re is not None:
                hit = extra_re.search(out)
                assert hit is None, (name, lang, hit.group(0) if hit else "")


def test_t30_absent_flag_equals_false_byte_for_byte():
    """« Absent = off » : aucune valeur par défaut permissive."""
    for name in _all_prompts():
        for lang in LANGS:
            absent = _render(name, lang)
            explicit_off = _render(name, lang, targets_enabled=False,
                                   target_selection_enabled=False)
            assert absent == explicit_off, (name, lang)


def test_t31_active_capability_never_leaks_registry_internals():
    tm = _manager(CONTRACT)
    flags = _flags(tm)
    assert flags["targets_enabled"] is True

    for name in _all_prompts():
        for lang in LANGS:
            out = _render(name, lang, **flags)
            for token in FORBIDDEN_EVEN_WHEN_ON:
                assert token not in out, (name, lang, token)


def test_t31_target_surface_stays_within_budget():
    """Mesure le bloc propre (inclusions déduites) et le compare au budget."""
    tm = _manager(CONTRACT)
    flags = _flags(tm)
    measured = {}

    for name, ceiling in BUDGET_TOKENS.items():
        for lang in LANGS:
            off = _render(name, lang, targets_enabled=False,
                          target_selection_enabled=False)
            on = _render(name, lang, **flags)
            delta = len(on) - len(off)
            # Déduire l'inclusion éventuelle (plan_grammar) déjà comptée
            # séparément pour ne pas imputer deux fois la même surface.
            if name == "planner.md":
                g_on = _render("plan_grammar.md", lang, **flags)
                g_off = _render("plan_grammar.md", lang,
                                targets_enabled=False,
                                target_selection_enabled=False)
                delta -= len(g_on) - len(g_off)
            tokens = delta // 4
            measured[(name, lang)] = tokens
            assert tokens <= ceiling, (name, lang, tokens, ceiling)

    # La mesure reste exposée pour le rapport : une régression de surface
    # doit être lue, pas seulement constatée.
    assert set(measured) == {
        (name, lang) for name in BUDGET_TOKENS for lang in LANGS
    }


def test_t32_invalid_contract_renders_exactly_like_no_contract():
    invalid = {"version": 1, "consumes": [{"param": "source"}]}
    tm = _manager(invalid)
    flags = _flags(tm)
    assert flags["targets_enabled"] is False
    assert flags["target_selection_enabled"] is False

    none_flags = _flags(_manager(None))
    assert none_flags == flags

    for name in _all_prompts():
        for lang in LANGS:
            with_invalid = _render(name, lang, **flags)
            without = _render(name, lang, **none_flags)
            assert with_invalid == without, (name, lang)

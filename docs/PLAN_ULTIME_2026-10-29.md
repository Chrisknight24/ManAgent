# Plan ultime — 2026-10-29

**Statut** : validé par Christian (GO), avec deux réserves intégrées :
1. **P1.1 (réparation automatique `label` → `output_target_name`) est SUPPRIMÉE.**
   Le harnais ne répare jamais une logique non prévue par le Planner : il
   contrôle et valide froidement. Pour ce cas, seule la robustesse du modèle et
   du prompt peut aider.
2. **P5 est reconçu** : pas de `ask_scope` (le système n'« ask » pas). L'entité
   parle en **langage naturel** ; un **méta-LLM routeur** choisit les providers
   et répond. Nom de l'outil à valider.

Ce document est la source de vérité pour éviter la perte de fil entre
compactions. Il est mis à jour à chaque phase.

---

## 0. Verdict froid sur les propositions de Christian

| # | Proposition | Verdict | Preuve |
|---|---|---|---|
| 1 | `any` doit laisser passer `result=false` | ✅ vrai | `core/executor.py:1478` renvoie déjà `True` ; seul `_verify_rigid_outcome` bloque (`:1626-1635`) |
| 1b | « on aurait vu fonctionner les conditionnalités » | ❌ faux pour ce plan | `execute_if` testait `$@_data_step_1 == "non"` ; `data_step_1` vaut `"non. On voit…"` ; comparaison exacte (`:1903-1904`) |
| 2 | `null`/absent = `"true"` rigide | ✅ vrai | `core/discovery/models.py:202` a déjà `default="true"` ; `PlanStep` est l'exception |
| 2b | défaut au niveau du champ `PlanStep` | ⚠️ piège | contaminerait `abstract_task` (critère sémantique) ; défaut au point d'usage `TOOL_CALL` |
| 3 | `false` reste un échec par défaut | ✅ vrai | `executor.py:1641-1673` |
| 4 | warning `any` non consommé, sans blocage LLM | ✅ vrai, ⚠️ canal | `perception_warnings_by_solver` est rendu dans `world_warnings` (texte faux) ; `plan_gaps` est une lacune **dure** (`convergence.md` règle 9) → 3e canal `expected_warnings` |
| 5 | target vs variable = prompt | ⚠️ partiel | `planner.md:135-136` et `plan_grammar.md:14-16` le disent déjà ; le modèle a quand même mis `label` sans `output_target_name` |
| 5b | réparation automatique | ❌ refusée par Christian | le harnais valide, il ne répare pas la logique |
| 6 | rapport contredit par le world state tronqué | ❌ attribution fausse | « Paint n'était pas ouvert » est le `error_reason` de `perceive_understand`, pas un rapport Presentator ; la perception lit le payload complet (250 000, `internal_tools.py:269`) |
| 6b | badge « trouvé » sur une réponse « non » | ✅ vrai, autre cause | `build_observability_report.py:803` + `_ABSENCE_MARKERS` (`:250-256`) ne connaissent pas « non » / « pas ouvert » |
| 7 | tokens mission vs session | ✅ vrai, ⚠️ | l'export ne contient que 138 255 / 11 appels ; le 1 091 705 / 98 appels vient d'une autre vue → problème de libellé |
| 8 | PD coûte cher, routeur nécessaire | ✅ vrai | `core/llm.py:319-404` ; ~1 026 tokens/appel, 5 providers |
| 9 | plus de français codé en dur | ✅ vrai, ⚠️ grave | `locale/en|fr/messages.po` = 0 octet, aucun `.mo` → `_()` no-op (`core/i18n.py:24-31`) |
| 10 | world state exprimé en targets + outils hôte | ✅ vrai, à moitié supporté | `alignment.py:84-90` projette mais `register=False` |

---

## 1. Table de vérité `expected_result`

| `expected_result` | résultat outil | convergence | warning |
|---|---|---|---|
| absent / `null` | `true` | ✅ | — |
| absent / `null` | `false` | ❌ (rigide `true`) | — |
| `"true"` | `true` | ✅ | — |
| `"true"` | `false` | ❌ | — |
| `"false"` | `false` | ✅ | — |
| `"false"` | `true` | ❌ | — |
| `"any"` | `true` | ✅ | si sortie non consommée |
| `"any"` | `false` | ✅ | si sortie non consommée |
| `"any"` | erreur infra (args invalides, JSON, exception) | ❌ | l'exécution avorte avant la convergence (`executor.py:1121-1124`, `:272`) |

**Règle** : `any` = « je ne juge pas la valeur, je branche dessus ». Un
`result=false` est une **valeur négative légitime**, pas un échec de plan.
`false` reste un échec par défaut. Seul `any` l'accepte.

---

## 2. Phases

### P0 — Débloquer les plans conditionnels (le vrai bug de la mission)

- [x] **P0.1** `any` passe `result=false` (`core/executor.py:1623-1673`).
  - Supprimer le rejet `raw_success_flag == "false"`.
  - Garde conservée : sans flag structuré + texte `erreur/error` → refus.
- [x] **P0.2** défaut `"true"` pour `tool_call`.
  - `PlanStep.expected_result: Optional[str] = None` + description.
  - `executor._check_convergence_inner` : `TOOL_CALL` → `step.expected_result or "true"`.
  - Supprimer le bypass `if not step.expected_result` pour `TOOL_CALL` (`:1558`).
  - `ABSTRACT_TASK` garde son critère sémantique.
- [x] **P0.3** présentation du résultat négatif.
  - `executor.py:1475-1478` : si `actual_data` existe, renvoyer la donnée, pas `"Erreur : …"`.
- [x] **P0.4** warning `any` non consommé, non bloquant.
  - `find_unconsumed_any_outputs(plan)` dans `plan_validator.py`.
  - Nouveau canal `runtime_state.expected_warnings_by_solver`.
  - `convergence.md` (base/en/fr) : bloc `expected_warnings`, texte neutre.
  - `plan_validation.md` : « sortie `any` non consommée = avertissement, jamais rejet ».
- [ ] **P0.5** règle de prompt (à intégrer par Christian, pas éditée ici) :
  « Avec `any`, brancher sur `$@_bool_<step>` (`== false`), jamais sur le texte
  libre `$@_data_<step>` : le texte peut être une description complète. »

**Tests** : `tests/test_any_conditional_plan.py` (9 cas, voir §5).

### P1 — Targets : clarté, pas de réparation

- [x] **P1.1 supprimé** : aucune réparation automatique. Le harnais ne devine
  pas la logique du Planner. Le cas Paint reste un échec de modèle/prompt.
- [ ] **P1.1'** robustesse prompt (Christian) : rôle target vs variable,
  exemple `label` + `output_target_name`, branchement sur `bool_`.
- [x] **P1.2** descriptions des champs (`core/plan_models.py:100-112`).
  - `output_variable_name` : valeur/donnée ; **pour une cible, utiliser
    `output_target_name`**.
  - `output_target_name` : nom local d'une cible produite par un outil
    `produces`, consommée comme argument `target`.
- [x] **P1.3** message de rejet non redondant.
  - Indice `output_target_name` émis **une seule fois** (`planner.py:443-447`).
  - Une seule piste groupée pour toutes les variables inconnues
    (`planner.py:513-529`), pas une liste de variables disponibles répétée 5×.
  - Budget cible : ≤ 1 200 chars pour 5 refs, au lieu de ~3 560.

**Tests** : `tests/test_target_rejection_message.py`, compléter
`test_planner_unknown_target_hint.py` et `test_feedback_hints.py`.

### P2 — World state lisible et exprimé en targets

- [x] **P2.1** rendu structuré, pas de coupe en plein mot (`core/alignment.py:98-111`).
  - Priorité : `summary_text` > focus/foreground > `windows` (une ligne par
    fenêtre) > `targets` > reste.
  - Retirer des champs entiers, jamais couper un mot ; `… (+N champs omis)`.
  - Le plafond reste déclaré par l'hôte (`world_snapshot.max_chars`, max 4000) :
    ne pas coder 2000 en dur.
- [x] **P2.2** règle de doc : `get_world_state` déclare `produces: targets`.
  - `docs/HOST_CONTRACT.md` + `TARGET_ROADMAP.md` §12.
  - Pour la réutilisation directe : `register=True` passif (recommandé) ou
    `adopt_target` explicite. Décision à prendre.
- [x] **P2.3** outils auxiliaires hôte (doc seulement) :
  `window_control(target, action: foreground|hide|close)`. Le cerveau ne code
  rien de spécifique : outils normaux à contrat de cibles.

**Tests** : `tests/test_world_snapshot_render.py`.

### P3 — Observabilité honnête

- [x] **P3.1** badge `found` basé sur `result` structuré
  (`build_observability_report.py:803`) ; fallback marqueurs complété
  (« non », « pas ouvert », « not open »).
- [x] **P3.2** tokens mission vs session.
  - `_session_usage` par `session_id` (somme des missions).
  - Header : `Tokens mission` + `Session` + `PD: ~N tokens/appel`.
- [x] **P3.3** event `world_snapshot` dans l'export (mission_id, source_tool,
  chars, truncated, hash). L'export actuel n'a **aucun** world state.

**Tests** : `tests/test_observability_truth.py`.

### P4 — Leçons + i18n

- [x] **P4.1** supprimer la double écriture de leçon
  (`core/learner.py:246-258`). Pas de préfixe français ; pas de copie avec le
  même embedding. Migration douce des doublons 476/477, 478/479 (script
  dry-run, pas d'écriture DB sans GO séparé).
- [x] **P4.2** i18n réelle.
  - `scripts/compile_i18n.py` (extraction AST + `.mo`), exécuté au démarrage si
    `.mo` absent/ancien.
  - Test de couverture `en`.
  - Corriger les 4 offenders : `learner.py:250`, `internal_tools.py:986`,
    `executor.py:1563`, `learner.py:144,287`.
  - Ne pas traduire les 700 chaînes maintenant ; interdire les nouvelles.

**Tests** : `tests/test_lessons_dedupe.py`, `tests/test_i18n_pipeline.py`.

### P5 — Routeur de scope (DETTE-3) — **reconçu**

**Principe** : l'entité ne choisit pas un `scope` technique. Elle **parle en
langage naturel** et dit ce qu'elle veut ; un **méta-LLM routeur** décide quels
providers consulter, puis répond. Si aucun provider ne peut traiter la demande,
le routeur le dit explicitement.

- [x] **P5.1** outil unique `investigate` (nom à valider ; alternatives :
  `explore`, `research`, `lookup`, `query`).
  - Args : `{question: str}` **uniquement** (pas d'enum de scope).
  - Description : « Pose une question en langage naturel sur l'état du monde,
    les fichiers, l'historique, les skills ou le registre. Le routeur choisit
    la source. »
- [x] **P5.2** méta-LLM routeur :
  - reçoit `question` + la liste **courte** des providers (`data_type` +
    `get_scope_description()`), pas leurs `targets` détaillés ;
  - renvoie `{providers: [...], reason: str}` ou `{providers: [], reason: "aucun scope..."}` ;
  - appelle les providers retenus, agrège, répond.
- [x] **P5.3** `DataProvider.answer(question) -> Optional[str]` optionnel
  (défaut `None`). Aucun provider existant à modifier.
- [x] **P5.4** refus explicite : `{result: false, error_reason: "Aucun scope ne
  peut traiter cette demande : <question>"}`. Ce n'est **pas** un rejet de plan.
- [x] **P5.5** flag `enable_scope_router`, défaut **off**. Off → section PD
  byte-identique. On → section PD ≤ 1 ligne (« Scopes disponibles : … »).
- [ ] **P5.6** mesure : delta tokens/appel, taux de refus, qualité des réponses.
  Activation après R1/R2.

**Tests** : `tests/test_scope_router.py` (routeur NL → provider ; refus ;
flag off byte-identique ; flag on ≤ 1 ligne ; pas de dispatch d'action).

### P6 — Docs / roadmap

- [x] `docs/TARGET_ROADMAP.md` : §3.3 (world state en targets, `max_chars`),
  §5.1 (tests P0-P5), §12 (monde en targets), §13 (routeur), journal 29/10.
- [x] `docs/HOST_CONTRACT.md` : `get_world_state` → `produces: targets` ;
  `window_control` optionnel.
- [x] `docs/` : note « Ce que `any` signifie » + table de vérité.

---

## 3. P5 — détail du routeur (design)

```text
Entité (Planner/Solver)
  └─ investigate(question="Est-ce que Paint est ouvert ?")
       └─ Méta-LLM routeur
            ├─ providers candidats (data_type + scope_description courte)
            ├─ décision : ["world"]  (ou [] si aucun)
            ├─ world.answer("Est-ce que Paint est ouvert ?")
            └─ réponse agrégée {result, data, sources}
```

**Pourquoi c'est plus simple** :
- le prompt de l'entité ne contient plus les `goals`/`targets` de 5 providers ;
- l'entité n'a pas à connaître les `data_type` : elle pose sa question ;
- le routeur est le seul à voir les descriptions de scope ;
- le refus est une réponse normale, pas une erreur de plan.

**Coût** : 1 appel LLM supplémentaire **seulement** quand l'entité explore.
À comparer aux ~1 026 tokens de PD injectés à chaque appel.

---

## 4. Rejeu de la mission `4a810e6e`

| Étape | Cause | Correctif |
|---|---|---|
| Tentative 1 | `$@_data_paint_ouvert` sans producteur | rejet correct |
| Tentative 2 | `any` + `result=false` rejeté | **P0.1** |
| Tentative 2 | `execute_if` sur texte libre | **P0.5** (brancher sur `bool_`) |
| Tentative 2 | trace `"Erreur : …"` | **P0.3** |
| Tentative 3 | `label` sans `output_target_name` | **P1.1'** prompt (pas de réparation) |
| Tentative 3 | message 5× redondant | **P1.3** |
| World state | 2 fenêtres/6, coupe « Age… » | **P2.1** + `max_chars` hôte |
| Export | badge « trouvé » sur « non » | **P3.1** |
| Tokens | mission vs session | **P3.2** |

**Verdict** : avec P0.1 + P0.3 + P0.5, la tentative 2 serait allée au bout de sa
logique conditionnelle ; avec P1.1' (prompt), la tentative 3 avait une chance.
Sans P1.1' (réparation refusée), le cas Paint reste un échec de modèle : c'est
assumé.

---

## 5. Tests (rouges d'abord)

### `tests/test_any_conditional_plan.py`
- `test_any_accepts_false_result`
- `test_any_does_not_abort_plan`
- `test_execute_if_on_bool_fires_after_any_false`
- `test_execute_if_on_free_text_does_not_fire`
- `test_any_unconsumed_emits_warning_not_refusal`
- `test_any_consumed_by_execute_if_emits_no_warning`
- `test_default_expected_result_is_true_for_tool_call`
- `test_abstract_task_keeps_semantic_expected_result`
- `test_negative_result_keeps_data_in_trace`

### `tests/test_target_rejection_message.py`
- `test_target_hint_emitted_once`
- `test_unknown_vars_grouped_in_one_hint`
- `test_message_budget_under_1200_chars_for_5_refs`

### `tests/test_world_snapshot_render.py`
- `test_no_mid_word_cut`
- `test_all_windows_listed_when_budget_allows`
- `test_priority_summary_first`
- `test_host_max_chars_honored`

### `tests/test_observability_truth.py`
- `test_found_uses_structured_result`
- `test_negative_answer_not_badged_found`
- `test_mission_and_session_tokens_are_labeled`

### `tests/test_lessons_dedupe.py`
- `test_no_prefixed_duplicate_lesson`
- `test_same_embedding_not_written_twice`

### `tests/test_scope_router.py`
- `test_natural_language_routes_to_provider`
- `test_no_provider_returns_explicit_refusal`
- `test_flag_off_keeps_pd_byte_identical`
- `test_flag_on_reduces_pd_to_one_line`

---

## 6. Règles de travail

- Tests scripts **rouges d'abord**, puis implémentation, puis suite complète
  (~90–120 s).
- Pas de retry identique sur erreur déterministe.
- Pas de suppression de targets : les targets avant d'agir sont cruciales.
- Pas de réparation automatique d'une logique non prévue par le Planner.
- Pas de commit/push sans **GO explicite**.
- Prompts : pas de fixette computer use ; rôle et cas d'usage, pas de
  « si/sinon ». Christian garde la main sur `llm_analyze_data.md`.

---

## 7. État du dépôt au moment de la rédaction

- ManAgent : **81 fichiers modifiés** non commités, HEAD `0ff00e6`.
- CondiFlow : `6ca30db` commité localement, **non poussé**.
- Suite de référence : **689 passed**.
- Interpréteur : `.venv\Scripts\python.exe`.

---

## 8. Journal

- 2026-10-29 : plan ultime rédigé après la mission `4a810e6e`.
- 2026-10-29 : P1.1 (réparation auto) **supprimée** sur décision de Christian.
- 2026-10-29 : P5 reconçu (langage naturel + méta-LLM routeur, pas `ask_scope`).

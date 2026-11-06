# TargetRegistry — Roadmap d'implémentation

**Statut : S9 implémenté** (2026-10-21). Tests verts (579). Prêt pour le pilote R1.
Ce document est la **source de vérité** du chantier ; il remplace
`CHANTIER_TARGET.md` (archivé) et complète `TARGET_REGISTRY.md` (spec de
conception, propriété de l'Expert).

- Spec : [`TARGET_REGISTRY.md`](TARGET_REGISTRY.md)
- Contrat hôte : [`HOST_CONTRACT.md`](HOST_CONTRACT.md) (mis à jour en S5/S6)
- Décisions verrouillées : voir §2
- Dette et reports : voir §8 (à ne jamais perdre)

---

## 1. Objectif et périmètre

Faire circuler des **cibles opaques et stables** entre l'hôte et ManAgent, sans
que le LLM voie ou invente des références brutes :

- l'hôte **produit** des cibles (perception) ;
- ManAgent les **nomme** localement, les garde dans un registre, et les
  **résout** au dernier moment vers la ref hôte ;
- l'hôte **consomme** une ref sur les outils d'action ;
- le LLM ne manipule que des **noms locaux** (`$@_bouton_fermer`), jamais des
  refs, ids, coordonnées ou index bruts.

**Dans le périmètre v1** : outils externes déclarés par manifeste, modes
`single`/`candidates`, updates/invalidations, génération (ABA), deux méta-outils
`perceive_understand` / `perceive_action`, erreurs fermées, inertie totale si
non déclaré.

**Hors périmètre v1** (assumé, ne pas promettre) :

- les désignations **construites** par le cerveau (chemins, adresses,
  coordonnées inventées) : elles restent des paramètres ordinaires, arbitrées
  par le refus de l'hôte ;
- la PD complète (F3) : après ce chantier (DETTE-3) ;
- la correction globale du quoting des variables string (DETTE-1) ;
- `target_failure` : après mesures (DETTE-4) ;
- la concurrence réelle multi-solvers : S8 conditionnel.

---

## 2. Décisions verrouillées

### 2.1 Christian

- **DC1 — Strict par défaut** : `raw: deny`. Une valeur de consommateur inconnue
  est refusée (`TARGET_NOT_FOUND`), sans dispatch. Échappatoire explicite
  `raw: allow` pour les hôtes legacy.
- **DC2 — `target_failure`** : après mesure (R2). Pas de nouvelle classe
  d'échec avant les données R6.
- **DC3 — Pilote** : un seul hôte = **CondiFlow**. `target_contract` absent =
  comportement strictement inchangé. Activation outil par outil après S0-S4.
  Critère de succès : une mission réelle (ex. VLC/apollo) avec cibles
  réutilisées, aucune ref inventée, zéro régression sur les outils non migrés.
- **DC4 — Méta-outils** : `output_target_name` autorisé sur
  `perceive_understand` / `perceive_action` (c'est le chemin de sélection). La
  **PD complète reste après** Target ; Target n'ajoute qu'une vue cible compacte.
- **DC5 — Quoting** : correction **reportée** après le pilote, tracée en
  DETTE-1. Contournement ciblé uniquement pour les paramètres consommateurs.
- **DC6 — Prompts dynamiques (P0)** : toute surface de prompt est pilotée par le
  manifeste. Sans déclaration, prompts **byte-identiques** à aujourd'hui ; avec
  déclaration, seuls les blocs utiles sont ajoutés, compacts, sans duplication.
  Détail en §3.3 et tests T30-T34.

### 2.2 Expert / architecture

- Membrane **bidirectionnelle** au même point : egress (résolution avant
  dispatch) + ingress (projection avant stockage).
- Point unique : `Orchestrator._execute_external_tool` (orchestrator.py:2476).
- Registre sur `runtime_state` partagé, **jamais** dans `variable_registry`.
- Pas d'« Ancreur » à machine à états : API de service + résolveur unique.
- Échec honnête : zéro retry local, zéro substitution, remontée Solver.
- Agnostique : aucun nom d'hôte/outil/app en dur ; inertie si non déclaré.
- Aucun bridage de sortie ; aucune baisse de `LLM_STRUCTURED_MAX_OUTPUT_TOKENS`.
- Un seul interrupteur : **`target_contract`**. Pas de `targets.enabled`.
- Clés mintées runtime, descriptions = **données non fiables** (délimitées,
  plafonnées), jamais des instructions.
- `output_target_name` = champ **séparé** de `output_variable_name`, sans
  normalisation `data_/bool_`, sans alias.
- Résolution **avant** `tool.requested` (qui EST le dispatch) ; `arguments`
  porte les refs, les artefacts LLM portent les noms.
- `dispatched` additif, lu avant classification.
- `target_updates` appliqué **puis retiré** des données stockées.
- Génération : `generation` gagne sur l'ordre du tableau ; un update sans
  génération n'écrase jamais un update avec génération.
- ABA : une ref invalidée n'est jamais réutilisée pour une autre cible ; une
  nouvelle entrée reçoit un **nouveau** `local_name` ; le nom de variable de
  plan reste stable (pointeur).

---

## 3. Architecture cible

```
Hôte ── tool.result (refs) ──► Orchestrator ──► Executor
                                      ▲              │
                                      │              ├─ 1. assets typés (existant)
                                      │              ├─ 2. projection ingress
                                      │              ├─ 3. TargetRegistry.apply
                                      │              └─ 4. stockage projeté (noms)
                                      │
Plan (noms) ── Executor ── consumer raw ──► _execute_external_tool
                                      │              ├─ classify (nom/brut)
                                      │              ├─ resolve (nom → ref)
                                      │              ├─ TARGET_* si invalide (no dispatch)
                                      │              └─ tool.requested {refs} + dispatched
                                      ▼
                                    Hôte
```

### 3.1 Composants

| Composant | Rôle | Fichier |
|---|---|---|
| `TargetRegistry` | stockage, génération, dédup, updates, purge | `core/targets.py` (nouveau) |
| `TargetContract` | parse/validate manifeste, consumers/producers | `core/target_contract.py` (nouveau) |
| substitution consumer-aware | transport brut des noms (pas de JSON quoting) | `core/executor.py` |
| résolution egress | nom → ref, erreurs, `dispatched` | `core/orchestrator.py` |
| projection ingress | retirer refs/updates, stocker les noms | `core/executor.py` |
| sélection | `single`/`candidates`, `perceive_*` | `tools/internal_tools.py` |
| validation plan | def-use paramètre-aware, littéraux | `core/plan_validator.py` |
| manifeste outils | `target_contract` hors vue LLM | `tools/tools_manager.py` |

### 3.2 Attaches et points exacts (vérifiés)

- `RuntimeState` : ajouter `self.target_registry` (runtime_state.py).
- Enregistrement outils : `ToolsManager.register_tool` (tools_manager.py:449) et
  `load_tools_from_payload` (:517). Le contrat va dans `_target_contracts`,
  **pas** dans `_tools` (sinon fuite dans la vue LLM).
- Egress : `_execute_external_tool` : validate (:2481), `call_id` (:2488),
  `tool.requested` (:2494), await (:2501).
- Interpolation : `_handle_tool_call` (executor.py:1009), `_interpolate_dict`
  (:764), `_interpolate_text` (:704). `_REFERENCE_FIELDS` (:43).
- Entrée variable : `{value, description, source, timestamp}` (executor.py:191).
- Ingress : parse (:1042), assets typés (:1083-1124), stockage (:1199-1208),
  `node.result` (:1218).
- Def-use : `find_unresolved_plan_refs` (plan_validator.py:240-289).
- Vue registre : `_get_registry_metadata_view` (solver.py:296),
  `SolverRegistryProvider` (core/discovery/providers/registry_provider.py).
- Reset/annulation : `generation_epoch` (orchestrator.py:2448),
  `cancel_requested` (:2441/2457), `manifest_updated` (:230).
- Recul/budget : `_recul_key_for` (solver.py:1367), `_maybe_extend_budget`
  (:1406), `FailureClass` (execution_models.py:24), learner (:225/303).

---

### 3.3 Surface de prompts : tout doit rester dynamique

**Principe (P0, non négociable)** : le prompt d'une entité est une **projection
du manifeste de l'hôte + de l'état runtime**, jamais un texte statique enrichi
pour tout le monde. Une capacité non déclarée ⇒ **aucune section, aucun
marqueur, aucun vocabulaire** correspondant. Un hôte sans `target_contract` doit
voir **exactement** les prompts d'aujourd'hui (à l'octet près).

Cela vaut pour **toutes** les entités, pas seulement le Planner : Planner,
Solver/Feasibility, Presentator, Convergence, Discovery, Orchestrator,
ToolsManager, analyseurs.

#### Capacités et blocs

| Capacité (manifeste/runtime) | Bloc autorisé | Défaut |
|---|---|---|
| `target_contract` actif | marqueur `(target)` dans la vue registre + note de sélection courte | **off** |
| source monde visible (`world_guidance_visible`) | `## ÉTAT ACTUEL DU MONDE` / `## LIRE LE MONDE` | déjà conditionnel |
| `returns` typés déclarés | note assets typés | off |
| skills disponibles | `## SKILLS PRÊTS` | off |
| PD activée | blocs PD existants | off |
| multimodalité non supportée | avertissement modalités | off |

> **Exception existante, hors périmètre** : le bloc monde garde sa logique
> actuelle `world_guidance_visible` (alignment.py:212), permissive quand aucun
> outil externe n'est déclaré. Target ne l'active **jamais** et ne le modifie
> pas ; un flag monde explicite serait un chantier séparé (DETTE-6).

#### Portée par entité (allowlist)

Une capacité globale n'injecte **rien** automatiquement partout : chaque entité
déclare les blocs qu'elle accepte.

| Entité | Blocs cibles autorisés |
|---|---|
| Planner | marqueur `(target)` dans le registre + note de sélection (si `candidates`) + grammaire `output_target_name` |
| Solver / Feasibility | marqueur `(target)` + note de sélection (si `candidates`) |
| Presentator | aucun (les noms sont déjà dans les variables) |
| Convergence | aucun |
| Discovery / Orchestrator / ToolsManager | aucun par défaut |

#### Grammaire du plan

`plan_grammar.md` est inclus **toujours**. Tout ce qui concerne les cibles
(`output_target_name`, syntaxe consommateur) doit être dans un **include
conditionnel** ou un bloc `{% if targets_enabled %}` ; sinon un hôte sans
contrat verrait des champs qu'il ne peut pas produire. En S4, un plan qui utilise
`output_target_name` sans contrat actif est rejeté avec un message explicite.

#### Vocabulaire et fraîcheur

- Flags calculés **à chaque rendu** (aucun cache de configuration) : un
  `manifest_updated` ou un reset doit changer la surface immédiatement.
- Quand `targets` est off, le prompt rendu ne doit contenir **aucun** jeton
  spécifique au registre : `target_contract`, `output_target_name`, `TARGET_`,
  `dispatched`, marqueur `(target)`. Test de scan (T30).
- Multilingue : tout nouveau bloc doit exister en `base`, `en`, `fr` (ou retomber
  sur `base`) ; T31 tourne sur les trois.
- Budget : surface cible ≤ ~200 tokens **et** ≤ ~3 % du prompt de l'entité ;
  au-delà, tronquer la vue (jamais les instructions critiques).
  **Mesure 2026-10-29** (bloc propre, inclusions déduites) : Planner
  **~304 tokens**, Feasibility **~126**, `plan_grammar` **~131**. Le
  Planner dépasse 200 à cause de l'exemple travaillé de la chaîne cible
  (F5) ; le plafond appliqué est donc **320 tokens** (`BUDGET_TOKENS` dans
  `tests/test_targets_prompt_surface.py`). La clause « ≤ 3 % » n'est pas
  applicable aux prompts courts (Feasibility : 126 tokens ≈ 7 % de
  1 753 chars) ; elle est remplacée par un plafond absolu par entité.
  Toute hausse doit être justifiée et mise à jour ici.

#### Règles d'implémentation

1. **Un seul calcul de flags** : fonction pure
   `prompt_capabilities(runtime_state, tools_view) -> set[str]`
   (`targets`, `world`, `typed_assets`, `skills`, `discovery`, ...). Aucun
   booléen éparpillé dans les entités.
2. **Absent = off** pour tout nouveau flag. Ne jamais reproduire le piège actuel
   `{% if world_guidance is not defined or world_guidance %}` (défaut
   permissif) : le bloc cible utilise `{% if targets_enabled %}` avec `False`
   explicite.
3. **Pas de section vide** : si le flag est faux ou si la vue est vide, le bloc
   entier disparaît (pas de titre orphelin, pas de `N/A`, pas de placeholder).
4. **Pas de duplication** : les noms de cibles sont déjà dans la vue registre
   (`$@_bouton_fermer`) ; on **enrichit la ligne existante** avec un marqueur
   compact `(target)` (via `type: "target"` rendu par
   `registry_compact`) et une description courte. On n'ajoute **pas** un
   second tableau de cibles. Une note de sélection n'apparaît que si un
   producteur `candidates` est réellement déclaré.
5. **Budget** : bloc cible ≤ ~10 lignes / ~200 tokens ; descriptions tronquées ;
   aucune ref, `correlation_id`, `generation` ni payload brut dans les prompts.
   Mesurer le delta de tokens par prompt (métrique), pas seulement à l'œil.
6. **Fraîcheur** : la vue est reconstruite à chaque tour ; cibles inactives
   retirées ; alias morts jamais affichés ; ABA reflété.
7. **Reconfiguration** : `manifest_updated` / reset → les flags sont recalculés ;
   retirer `target_contract` fait disparaître le bloc immédiatement.
8. **Fallback Jinja2** : `_fallback_render` (prompt_loader.py) supprime
   aujourd'hui les balises `{% %}` mais **laisse le contenu** des blocs
   conditionnels. Il doit devenir conservateur (supprimer le contenu des blocs
   `if` non évaluables) ou refuser de rendre un template conditionnel. Sinon un
   environnement sans Jinja2 afficherait les sections cibles à tout le monde.
9. **Points d'ancrage** : `PromptLoader.load` (Jinja2 + filtres
   `registry_compact`/`args_sig`), `prompts/base/*.md` (+ `en/`, `fr/`),
   `planner.py:163-266`, `solver.py:1155-1169`. `world_guidance` est déjà
   calculé ; on suit le même modèle, avec défaut off pour les nouveautés.
   **Ne pas réutiliser la variable `targets`** (déjà utilisée par la
   découverte dans `explorer_plan_generation.md`) : le flag s'appelle
   `targets_enabled`.

#### Tests dédiés

- **T30** : sans aucune capacité déclarée → prompts de **toutes** les entités
  byte-identiques à la référence (golden).
- **T31** : `target_contract` actif → seuls les blocs autorisés apparaissent ;
  aucun ref/correlation/génération ; budget respecté.
- **T32** : contrat invalide/partiel → prompt de base + diagnostic, jamais un
  bloc partiel.
- **T33** : fraîcheur/inactivité/ABA → aucun nom mort ; vue reconstruite.
- **T34** : indépendance des flags → `targets` actif n'implique pas `world`, et
  inversement.

---

## 4. Contrat (résumé exécutable)

### 4.1 Manifeste d'outil

```json
"target_contract": {
  "version": 1,
  "consumes": [
    {"param": "target", "scope": "ui_element", "required": true}
  ],
  "produces": [
    {
      "path": "data.targets",
      "mode": "candidates",
      "scope": "ui_element",
      "fields": {
        "ref": "id",
        "description": "label",
        "correlation_id": "corr",
        "generation": "gen"
      },
      "usable_as": ["ui_element"]
    }
  ]
}
```

Règles :

- `version` obligatoire ; absent → inerte (warning de lint si `targets` seul).
- `consumes[].raw` : `deny` (défaut) refuse toute valeur non liée ;
  `allow` est l'échappatoire explicite pour les hôtes legacy (passthrough).
- `consumes[].param` : nom du paramètre hôte ; **interdit** : `source`,
  `source_data`, `sources` (réservés par `_REFERENCE_FIELDS`). Un paramètre
  consommateur ne doit **pas** déclarer d'`enum` (le planner passe un nom local,
  pas une valeur hôte) — erreur de lint.
- `produces[].path` : pointeur JSON relatif au résultat (ex. `data.targets`).
- `mode` : `single` (exactement une cible, auto-sélectionnée) ou `candidates`
  (liste, sélection requise).
- `fields.ref` obligatoire ; `scope` XOR `fields.scope` (pas les deux).
- `usable_as` : « une cible de ce scope peut remplir un slot de ce scope » ;
  non transitive ; IDs validés.
- `ref` canonique : JSON trié, refus `NaN`/`Infinity` →
  `TARGET_SCHEMA_INVALID` (diagnostic, pas exception).
- Description = donnée non fiable, plafonnée (ex. 200 car.), délimitée.

### 4.2 Résultat runtime

```json
{"result": true,  "data": {...}, "dispatched": true,
 "targets": [{"key": "cand_...", "description": "...", "scope": "..."}],
 "target_updates": [{"correlation_id": "...", "op": "invalidate"}]}

{"result": false, "data": null, "dispatched": false,
 "error": {"code": "TARGET_NOT_FOUND", "message": "..."}}
```

### 4.3 Codes fermés

`TARGET_NOT_FOUND`, `TARGET_INACTIVE`, `TARGET_SCOPE_MISMATCH`,
`TARGET_STALE_REF` (refus hôte mappé), `TARGET_SCHEMA_INVALID`.
Un refus hôte reste `result.status: "rejected"` + `error.code` (loi L1), pas un
code runtime. `TARGET_REF_REJECTED` n'est **pas** réintroduit.

`correlation_id` partout (jamais `correlation`).

### 4.4 Lois hôte (HOST_CONTRACT)

- **L1** : « rejeter » = aucun effet (atomique).
- **L2** : une ref invalidée n'est jamais réutilisée pour une autre cible sans
  `generation`.
- **L3** : un update/invalidation causé par une action est livré au plus tard
  avec le résultat de cette action.
- **L4** : l'hôte n'agit jamais sur un substitut ressemblant.
- **L5** : les descriptions de cibles sont des données non fiables.

---

## 5. Matrice de tests

Chaque test est **rapide** (sans LLM) sauf mention contraire ; les tests
d'intégration utilisent `HostSim`.

| ID | Vérifie |
|---|---|
| T1 | manifeste valide → contrats enregistrés ; registre inerte sans contrat |
| T2 | consommateur `$@_cible` → nom → ref hôte ; le LLM ne voit que le nom |
| T3 | valeur brute inconnue → `TARGET_NOT_FOUND`, `dispatched:false` |
| T4 | `raw: allow` → passthrough explicite |
| T5 | plusieurs paramètres cibles : un invalide → **aucun** dispatch |
| T6 | manifeste invalide (scope XOR, ref manquante, NaN, reserved name) |
| T7 | cible inactive → `TARGET_INACTIVE`, no dispatch |
| T8 | scope incompatible → `TARGET_SCOPE_MISMATCH`, no dispatch |
| T9 | `candidates` → sélection 0/1/N ; clés mintées ; pas de ref brute |
| T10 | `single` → auto-sélection ; description exposée |
| T11 | `output_target_name` sur `perceive_understand` → nom lié et réutilisable |
| T12 | `perceive_action` → sélection sur données **projetées** ; clé mintée |
| T13 | recul → snapshot frais si source monde ; pas de registre périmé |
| T14 | inertie : sans contrat, comportement et prompts identiques (golden) |
| T15 | renommage des champs `returns` → projection toujours correcte |
| T16 | clé candidate inconnue → `TARGET_NOT_FOUND` avant dispatch |
| T17 | hôte qui rejette toujours → terminaison démontrée |
| T18 | canary : aucune ref hôte dans les artefacts **LLM-facing** (exclut
  `tool.requested`/`tool.result`) |
| T19 | sortie unique : tout dispatch hôte passe le chokepoint (instrumentation) |
| T20 | annulation/purge pendant un appel en vol → pas de crash, pas de faux
  `TARGET_NOT_FOUND` (résolution figée avant dispatch) |
| T21 | dédup : `correlation_id`, sinon `ref` canonique ; sinon nouvelle entrée |
| T22 | génération : supérieure gagne ; absente n'écrase jamais présente |
| T23 | ABA : alias invalidé jamais réutilisé ; nouveau `local_name` ; rebind
  documenté |
| T24 | `manifest_updated` / reset epoch → purge correcte (`all:true`) |
| T25 | snapshot monde : projection seulement, **aucune** inscription de cible |
| T26 | `target_updates` appliqué avant l'étape suivante puis **retiré** |
| T27 | mixte texte + cible dans un consommateur → **rejeté**, jamais trituré
  silencieusement (note Expert a) |
| T28 | prompts **byte-identiques** quand `target_contract` absent |
| T29 | concurrence (S8) : méthodes de registre atomiques / pas de coroutine |
| T30 | sans capacité déclarée → prompts de **toutes** les entités byte-identiques |
| T31 | `targets` actif → blocs autorisés seulement ; zéro ref ; budget respecté |
| T32 | contrat invalide/partiel → prompt de base + diagnostic, jamais partiel || T33 | fraîcheur : cibles inactives/ABA absentes de la vue ; pas de nom mort |
| T34 | flags indépendants : `targets` n'implique pas `world`, et inversement |

> **Couverture T30-T32 (2026-10-29)** : `tests/test_targets_prompt_surface.py`
> étend le scan à **toutes** les entités × 3 langues. T30 scanne aussi le
> vocabulaire du **mécanisme** cible (`cell`, `uia`, `grid`, `grille`,
> `ribbon`, `ruban`, `zoom`, `drag`, `glisser`) et les verbes d'action UI
> pour `planner`/`feasibility`/`plan_grammar` ; T30bis vérifie
> « absent = off » à l'octet près ; T31 mesure le budget ; T32 compare le
> rendu contrat invalide au rendu sans contrat, entité par entité.

### 5.1 Traçabilité (vérifiée)

Une matrice n'est utile que si elle est auditable : « T1-T34 exécutés » ne se
vérifie pas en relisant dix fichiers. `tests/test_targets_traceability.py`
porte la table `MATRIX` (T-ID → fichiers + fonctions) et vérifie à chaque
suite que :

- les **34 IDs** sont déclarés exactement une fois ;
- chaque **fonction citée existe réellement** (échec si un test est renommé
  ou supprimé) ;
- la table ne s'appuie pas sur un seul fichier.

Répartition actuelle :

| Fichier | IDs |
|---|---|
| `test_targets_contract.py` | T1, T6 |
| `test_targets_egress.py` | T2, T3, T4, T5, T7, T8, T20, T27 |
| `test_targets_selection.py` | T9, T10, T11, T12, T16 |
| `test_targets_ingress.py` | T15, T18, T21, T22, T23, T25, T26 |
| `test_targets_s4.py` | T31, T32, T33, T34 |
| `test_targets_s6.py` | T17, T19, T24 |
| `test_targets_s7.py` | T13 |
| `test_targets_s8.py` | T29 |
| `test_targets_inertie.py` | T14 |
| `test_targets_prompt_surface.py` | T28, T30, T31, T32 |

La table fait foi côté code ; ce tableau n'est qu'un index de lecture. Toute
modification de `MATRIX` doit être répercutée ici.

### 5.2 Tests P0-P5 (2026-10-29/31)

Ces tests ne portent pas d'ID T : ils verrouillent des règles de vérité
(attentes, rendu, i18n, routeur) indépendantes du registre de cibles.

| Fichier | Ce qui est verrouillé |
|---|---|
| `test_any_conditional_plan.py` | `any` accepte `result=false` ; `null` = `"true"` rigide ; warning de sortie non consommée, jamais un refus |
| `test_target_rejection_message.py` | indice `output_target_name` émis une fois ; variables inconnues groupées ; budget < 1 200 chars |
| `test_world_snapshot_render.py` | pas de coupe en plein mot ; priorité summary > focus > windows > targets ; cap d'entrées |
| `test_observability_truth.py` | badge `found` sur `result` structuré ; tokens mission/session ; event `world_snapshot` |
| `test_lessons_dedupe.py` | pas de préfixe français ; pas de double écriture au même embedding |
| `test_i18n_pipeline.py` | extraction `_()` → `.po` → `.mo` chargeable ; msgstr vides non compilés |
| `test_scope_router.py` | routage NL → provider ; refus explicite ; flag off byte-identique ; flag on = 1 ligne ; aucune action dispatchée |

---

## 6. Étapes d'implémentation

Chaque étape = un commit indépendant, tests verts, aucun changement de
comportement tant que `target_contract` est absent. La suite doit rester à
**564** tests après S4.

### S0 — Contrat + registre inerte (implémenté le 2026-10-20)

**But** : poser les fondations sans toucher au dispatch ni aux prompts.

- `core/targets.py` : modèles (`TargetRef`, `TargetEntry`, `TargetUpdate`,
  `TargetSelection`, `CandidateIndex`), `TargetRegistry` **synchrone**, clés,
  génération, dédup, purge, `snapshot_view`, canonicalisation.
- `core/target_contract.py` : parse/validate du manifeste, `consumer_param_names`,
  `producer_specs`, diagnostics, refus des noms réservés.
- `tools/tools_manager.py` : `_target_contracts` privé, `register_tool(...,
  target_contract=None)`, `load_tools_from_payload` transmet,
  `get_target_contract(name)` ; **ne pas** exposer dans `_tools`.
- `runtime_state.target_registry` initialisé ; purge au configure/reset.
- `PlanStep.output_target_name` (champ optionnel, inutilisé en S0).
- Lint dev : manifeste invalide → diagnostic clair ; `targets` sans
  `target_contract` → warning ; noms réservés → erreur ; `enum` sur un
  consommateur → erreur.
- Gate : aucune activation sans `target_contract`.
- `core/prompt_capabilities.py` (nouveau) : fonction pure
  `prompt_capabilities(...)`, **défauts off**, indépendance des flags.
- `prompt_loader._fallback_render` : ne doit **jamais** laisser fuiter le
  contenu d'un bloc conditionnel (cf. §3.3 règle 8).
- Aucun template modifié en S0 ; la surface cible arrive en S4.

**Tests** : T1, T6, T14, T28, T30 + unités registre/canonicalisation/flags.

**Fichiers** : `core/targets.py` (new), `core/target_contract.py` (new),
`core/prompt_capabilities.py` (new), `core/runtime_state.py`,
`tools/tools_manager.py`, `core/plan_models.py`, `core/prompt_loader.py`,
`tests/test_targets_contract.py`, `tests/test_targets_registry.py`,
`tests/test_targets_inertie.py`, `tests/test_prompt_capabilities.py`.

**Acceptation** : suite verte ; prompts byte-identiques ; aucun appel hôte
modifié ; `git diff` limité aux fondations.

### S1 — Membrane egress (implémenté le 2026-10-21)

- `executor.py` : substitution **consumer-aware brute** avant
  `_interpolate_dict` (target → valeur brute ; texte → `for_json=False` ;
  `skip_keys += consumer_keys`), y compris `source_args` des méta-outils.
- `orchestrator._execute_external_tool` : classification + résolution **avant**
  `tool.requested` ; `dispatched` ; erreurs fermées ; multi-cibles atomique ;
  unwrap `TargetRef` ; `arguments_display` optionnel.
- Exemption enum consommateurs aux **deux** sites (`_prevalidate_external`,
  `find_incomplete_delegated_args`).
- Tests : T2-T5, T7, T8, T20, T27.
- Acceptation : l'hôte reçoit des refs ; les variables/historique/prompts
  reçoivent des noms ; échec = aucun dispatch.

**Implémentation** : `consumer.raw` (`deny` par défaut, `allow` explicite) ;
substitution brute exacte dans `executor` (y compris `source_args`/`action_args`
des méta-outils) ; résolution tout-ou-rien dans `_execute_external_tool` avant
`tool.requested`, refs brutes sous le nom du paramètre, `arguments_display`
(noms) pour l'UI seulement, erreurs fermées + `dispatched:false`, aucun dispatch
sur échec ; `dispatched:true` ajouté au résultat **uniquement** si un contrat
actif existe ; exemption `enum` aux deux sites. 12 tests ajoutés (suite 537
verte).

### S2 — Membrane ingress (implémenté le 2026-10-21)

- `executor` : projection **après** assets typés (:1083-1124), avant stockage
  (:1199) ; `node.result` projeté ; `target_updates` appliqué puis retiré.
- Registre : dédup, génération, ABA, inactivations.
- Snapshot : projection seulement, aucune inscription (T25).
- `tool.result` : payload entrant jamais loggué/rendu LLM brut (T18).
- Tests : T15, T18, T21-T23, T25, T26.
- Acceptation : aucune ref dans `variable_registry`/historique ; updates avant
  l'étape suivante.

**Implémentation** : `core/target_ingress.py` (projection par `ProducerSpec`,
`apply_target_updates`, `strip_target_updates`) ; `TargetRegistry.find_by_correlation`
et `find_by_ref` ; projection dans `executor` après les assets typés et avant
stockage, `node.result` inclus ; updates appliquées même sur résultat en échec
puis retirées du payload ; snapshot `register=False` (projection seule, cibles
inconnues omises). 8 tests ajoutés (suite 545 verte).

### S3 — Sélection et méta-outils (implémenté le 2026-10-21)

- `perceive_understand` / `perceive_action` : perception → projection →
  sélection ; `single` auto ; `candidates` via `TargetSelection` (3 issues) ;
  clés + descriptions ; `$TARGET` reçoit la ref ; `output_target_name` lie le
  nom (`kind: "target"` dans l'entrée de variable).
- `perceive_action` ne grounde plus sur le brut (F15).
- Appel direct d'un producteur `candidates` = erreur ; `single` enveloppé =
  erreur.
- Tests : T9-T12, T16, T23.
- Acceptation : aucune ref brute dans les artefacts LLM ; action sur cible
  fraîche.

**Implémentation** : `core/target_ingress.py` expose `collect_projected_targets`
et `selection_from_payload` (`none`/`single`/`candidates`). `perceive_action`
projette l'observation avant toute sélection, auto-sélectionne `single`, fait
choisir une **clé locale** parmi `candidates`, puis substitue `$TARGET` par la ref
au dernier moment ; `perceive_understand` sélectionne et lie `output_target_name`
(`type`/`kind: "target"`, valeur = nom local). Les updates sont appliquées dans
les méta-outils. Sans contrat : chemin legacy brut inchangé (F15 seulement avec
contrat). 7 tests ajoutés (suite 552 verte).

### S4 — Validation de plan et UX contrat (implémenté le 2026-10-21)

- Def-use **paramètre-aware** : consommateurs = variables ∪ noms de cibles
  antérieurs ; autres = variables seulement. Littéraux (V2) validés.
- `output_target_name` : uniquement sur producteur ou méta-outil avec sélection ;
  collision de noms interdite ; charset.
- Descriptions augmentées F6 : **uniquement** consommateurs, **uniquement** si
  contrat actif.
- Surface prompts minimale (§3.3) : marqueur `(target)` dans la ligne
  de registre existante ; note de sélection **seulement** si un producteur
  `candidates` est déclaré ; aucun second tableau ; budget respecté.
- Tests : T2, T6, T15, T31-T34 + unités validateur ; T31 sur `base`/`en`/`fr` ;
  scan de vocabulaire T30 ; `plan_grammar` conditionnel.
- Acceptation : plan invalide rejeté avant exécution, message clair ; sans
  contrat, prompts inchangés (aucun jeton cible) ; budget respecté.

**Implémentation** : `find_target_plan_problems` (`core/plan_validator.py`)
valide les alias (`output_target_name`), les collisions, le charset, le préfixe
interdit, et le def-use paramètre-aware : un alias antérieur n'est légal que
dans un argument consommateur ; ailleurs (ou avant production) il est rejeté.
Les littéraux consommateurs exigent `raw: allow`. Les garde-fous S3 sont
appliqués au plan : producteur `candidates` direct refusé, `single` enveloppé
refusé. `planner._validate_plan` et `PlanValidator.validate` branchent le gate.
Descriptions des arguments consommateurs augmentées (`[target]`) uniquement
pour les contrats effectifs, sans muter le manifeste. Prompts : flags
`targets_enabled` / `target_selection_enabled` calculés une fois, marqueur
`(target)` dans la ligne de registre, note de sélection seulement si
`candidates`, grammaire `output_target_name` conditionnelle, blocs `base`/`en`/`fr`.
Sans contrat : aucun jeton cible. 12 tests ajoutés (suite 564 verte).

### S5 — HOST_CONTRACT (FR/EN) (implémenté le 2026-10-21)

- Documenter : manifeste, `consumes`/`produces`, modes, opacité des refs, lois
  L1-L5, codes, génération/ABA, `target_updates`, `raw deny/allow`, noms
  réservés, `dispatched`, `arguments_display`, non-bridage.
- Documenter la **surface de prompts pilotée par le manifeste** : ce que l'hôte
  déclare est ce qui apparaît ; sans déclaration, prompts inchangés à l'octet.
- Aucun code ; T28 reste vert.
- Acceptation : un hôte peut implémenter sans lire le code.

**Implémentation** : sections `3i. Cibles opaques` dans
`docs/HOST_CONTRACT.md` et `docs/en/HOST_CONTRACT.md` : déclaration
`target_contract`, consommateurs/producteurs, `raw`, `usable_as`, codes,
génération/dédup/ABA, `target_updates`, `dispatched`, `arguments_display`,
surface de prompts et lois L1-L5. `docs/host.manifest.schema.json` et
`docs/host.manifest.example.json` documentent le champ, avec un producteur
`candidates` et un consommateur valides. Aucun code runtime modifié ; suite
inchangée à 564 tests.

### S6 — Surface hôte (implémenté le 2026-10-21)

- `arguments_display`, `dispatched:false`, enveloppe `error`, ordre des events,
  `manifest_updated`, `target_updates` dans le résultat.
- Tests : T17, T19, T24.
- Acceptation : HostSim + un hôte réel minimal.
- Implémentation : `tests/host_sim.py` (banc protocole minimal, vrais
  `ToolsManager` / `TargetRegistry` / handlers de paquets de l'Orchestrateur,
  sans LLM ni UI). Aucun code runtime modifié : la surface existante est
  verrouillée par `tests/test_targets_s6.py` (6 tests). Suite : **570**.
- Limite assumée : l'hôte externe CondiFlow n'est pas lancé en S6 ; le smoke
  réel reste cadré par R1. Le HostSim complet (génération/ABA/rejets variés)
  reste en S9.

### S7 — Recul / snapshot (implémenté le 2026-10-21)

- Vérifier que le recul refait un snapshot (conditionnel à
  `world_guidance_visible`) et ne réutilise pas un registre périmé ; T13.
- Constat : `_check_feasibility` et `Planner.propose_plan` relisent déjà le
  snapshot à chaque appel (aucun cache de monde) ; aucun changement de flux.
- Correctif : `target_is_live(registry, local_name)` dans `core/targets.py` ;
  les alias de variables `type == "target"` dont la cible est invalide/purgée
  sont filtrés de `Solver._get_registry_metadata_view` et de l'`enriched_registry`
  du Planner. Le registre de mission reste la seule vérité.
- Tests : `tests/test_targets_s7.py` (4 tests : helper, vue Solver, prompt
  Planner, deux lectures de monde successives A→B). Suite : **574**.
- Aucune surface hôte modifiée ; `HOST_CONTRACT.md` inchangé.

### S8 — Concurrence (conditionnel, fermé le 2026-10-21)

- Si parallélisme réel : lock/atomicité ; sinon documenter « sync, pas de lock »
  + test T29 (aucune coroutine).
- Constat : aucun `await` sur le registre, aucune méthode `async` ; les
  mutations restent synchrones sur la boucle unique. Pas de lock ajouté.
- Test : `tests/test_targets_s8.py` (T29, introspection des méthodes publiques).
- Si un parallélisme réel multi-thread apparaît un jour, rouvrir ce chantier :
  le lock ne sera pas gratuit et devra être mesuré.

### S9 — Matrice complète + HostSim (implémenté le 2026-10-21)

- `HostSim` : manifeste, producteurs/consommateurs, updates, rejets, génération.
- Exécuter T1-T34 ; golden d'inertie ; métriques de base.
- Acceptation : tout vert, prêt pour le pilote.
- Implémentation : `tests/test_targets_s9.py` (4 tests d'intégration HostSim) :
  génération supérieure gagne / ancienne ignorée, update pendant action puis
  refus ferme `TARGET_INACTIVE`, purge entre scan et click
  (`TARGET_NOT_FOUND`, zéro dispatch), item producteur invalide rejeté sans
  bloquer la cible valide.
- Couverture : T1-T34 déjà répartis entre `test_targets_contract.py`,
  `test_targets_inertie.py`, `test_targets_egress.py`, `test_targets_ingress.py`,
  `test_targets_selection.py`, `test_targets_registry.py`, `test_targets_s4.py`,
  `test_targets_s6.py`, `test_targets_s7.py`, `test_targets_s8.py` ; S9 ajoute
  les scénarios de bout en bout manquants. Suite : **579**.
  Depuis le 2026-10-29, la répartition est **vérifiée automatiquement**
  (`tests/test_targets_traceability.py`, §5.1) : plus d'ID orphelin ni de
  test renommé sans mise à jour de la matrice.
- Limite maintenue : le smoke réel CondiFlow reste R1 ; aucun nom d'hôte/outil
  en dur, aucun contrat = inertie.

---

## 7. Déploiement

### R1 — Pilote CondiFlow (DC3)

- Ajouter `target_contract` à **1-2 outils** (ex. `vision`, `click`) dans le
  manifeste CondiFlow ; les autres restent sans contrat.
- Missions réelles : 1-2 (ex. VLC/apollo).
- Critères : cibles réutilisées ; aucune ref inventée ; zéro régression sur les
  outils non migrés ; prompts non gonflés ; métriques enregistrées.
- Rollback : retirer le bloc → comportement ancien immédiat.

### R2 — Mesure et décision

- Mesurer : taux de réutilisation, re-perception, alias morts, échecs
  mal classés.
- Décider `target_failure` (DC2) **après** données.
- Consigner ici.

### R3 — Généralisation et suites

- Étendre outil par outil ; mettre à jour README/CHANGELOG.
- Puis PD complète (DETTE-3), puis hygiène prompts (DETTE-5).

---

## 8. Dettes et reports (à ne pas perdre)

- **DETTE-1 — Quoting des variables string (préexistant, hors v1)** :
  `_interpolate_dict(..., for_json=True)` quote toute string (`$@_x` →
  `"btn_close"`). Contournement S1 : substitution brute des consommateurs.
  Correction propre après pilote (retourner la valeur brute pour un exact-match
  dans `_interpolate_dict`, + tests hôte/non-régression). À mentionner dans le
  README TargetRegistry : « hors périmètre, connu ».
- **DETTE-2 — CI** : workflow Windows + `vec0.dll` via `sqlite-vec` (différé).
- **DETTE-3 — PD complète (F3)** : après Target v1 + pilote.
- **DETTE-4 — `target_failure`** : après mesures R2.
- **DETTE-5 — Hygiène prompts** : après PD.
- **DETTE-6 — Flag monde explicite** : `world_guidance_visible` reste permissif
  quand aucun outil externe n'est déclaré (hôte chat pur). Un flag manifeste
  explicite (`world: off`) serait un chantier séparé ; Target n'y touche pas.

## 9. Notes Expert (non bloquantes, à garder)

- **N1 — mixte texte + cible** : `"prefix_$@_x"` sur un consommateur ne fait pas
  d'exact-match → texte brut → rejet (`TARGET_NOT_FOUND` ou `raw: deny`). C'est
  cohérent ; T27 le documente. Ne jamais « triturer » silencieusement.
- **N2 — marqueur de cible** : vérifié, `kind` n'est **pas** exposé aujourd'hui
  par `SolverRegistryProvider` ni par `_get_registry_metadata_view`. Décision :
  utiliser `type: "target"` (visible comme `(target)` dans `registry_compact`,
  voulu et minimal) ; aucune autre métadonnée n'est exposée. Si un jour `kind`
  s'étend (ex. `raw_credential`), ajouter une politique de filtrage.
- **N3 — DETTE-1** : ne pas corriger en S1 ; chantier séparé après pilote.

---

## 10. Checklist avant chaque commit

- [ ] `pytest -q` vert (base 804 au 2026-11-06).
- [ ] T14/T28 (inertie/prompts) verts.
- [ ] Aucun nom d'hôte/outil/app en dur.
- [ ] Aucun bridage de sortie ajouté.
- [ ] `target_contract` absent → zéro changement visible.
- [ ] Prompts : aucune capacité ⇒ byte-identique ; pas de section vide/dupliquée ;
      delta de tokens mesuré (budget §3.3).
- [ ] Docs FR/EN synchronisées si surface hôte modifiée.
- [ ] Pas de secret, pas de `REVIEWER.md`, pas de fichier hors périmètre.

---

## 11. Journal

- 2026-10-20 : roadmap créée ; GO S0 confirmé par l'Expert ; DC1-DC5 actés par
  Christian ; dettes et notes Expert consignées.
- 2026-10-20 : ajout de la règle **P0 « prompts dynamiques »** (§3.3) : surface
  pilotée par le manifeste, défauts off, zéro section inutile, budget et tests
  T30-T34 ; le fallback Jinja2 ne doit pas fuiter les blocs conditionnels.
- 2026-10-20 : **S0 implémenté**. `core/targets.py`, `core/target_contract.py`,
  `core/prompt_capabilities.py`, câblage `ToolsManager`/`RuntimeState`,
  `PlanStep.output_target_name`, fallback Jinja2 conservateur. 56 tests ajoutés
  (suite 525 verte). Aucun template modifié ; T28/T30 verts.
- 2026-10-21 : **S1 implémenté**. `consumer.raw` (`deny`/`allow`),
  substitution brute exacte (executor + méta-outils), résolution tout-ou-rien
  avant `tool.requested`, refs brutes sous le nom du paramètre, `arguments_display`
  (noms) pour l'UI, erreurs fermées + `dispatched:false`, `dispatched:true`
  seulement si contrat actif, exemption enum aux deux sites. 12 tests ajoutés
  (suite 537 verte). Aucun prompt modifié.
- 2026-10-21 : **S2 implémenté**. `core/target_ingress.py` (projection des
  producteurs, updates), `find_by_correlation`/`find_by_ref`, projection dans
  `executor` avant stockage et dans `node.result`, `target_updates` appliquées
  puis retirées, snapshot en projection seule (`register=False`). 8 tests
  ajoutés (suite 545 verte). Aucun prompt modifié.
- 2026-10-21 : **S3 implémenté**. Sélection `none`/`single`/`candidates` dans
  `perceive_understand`/`perceive_action`, projection avant grounding, clés
  locales seulement (aucune ref LLM), `output_target_name` lié en variable
  `type`/`kind: "target"`, `$TARGET` substitué au dernier moment, updates
  appliquées. Chemin legacy sans contrat inchangé. 7 tests ajoutés (suite 552
  verte). Aucun prompt modifié.
- 2026-10-21 : **S4 implémenté**. Gate cible paramètre-aware
  (`find_target_plan_problems`) : alias seulement dans un consommateur, littéraux
  contrôlés (`raw: allow`), `output_target_name` (charset, préfixe, collisions,
  contrat actif), `candidates` direct / `single` enveloppé refusés. Arguments
  consommateurs marqués `[target]` (contrat effectif seulement). Prompt :
  marqueur `(target)`, note de sélection conditionnelle, grammaire
  `output_target_name` conditionnelle (`base`/`en`/`fr`). Sans contrat : zéro
  jeton cible, prompts inchangés. 12 tests ajoutés (suite 564 verte).
- 2026-10-21 : **S5 implémenté**. Documentation hôte FR/EN complète dans
  `HOST_CONTRACT.md` (section 3i) : manifeste, consommateurs/producteurs,
  modes, opacité, codes, génération/ABA, `target_updates`, `raw`, noms réservés,
  `dispatched`, `arguments_display`, lois L1-L5, surface de prompts et
  non-bridage. Schéma et manifeste d'exemple mis à jour. Aucun code ; suite
  inchangée à 564 tests.
- 2026-10-21 : **S6 implémenté**. `tests/host_sim.py` + `tests/test_targets_s6.py`
  (6 tests) : `arguments_display`/`dispatched`, refus d'enveloppe sans event,
  `target_updates` appliquées sur échec, purge `manifest_updated`, hôte qui
  rejette toujours (T17), chokepoint unique (T19). Aucun code runtime modifié ;
  suite **570** verte.
- 2026-10-21 : **S7 implémenté**. `target_is_live` + filtrage des alias cibles
  mortes dans les vues Solver/Planner ; T13 vérifié : deux reculs successifs
  relisent le monde (A puis B), aucun registre périmé dans les prompts.
  4 tests ajoutés (suite **574** verte). Aucune surface hôte modifiée.
- 2026-10-21 : **S8 fermé**. Registre synchrone (aucune coroutine, aucun `await`),
  boucle unique : pas de lock ajouté. T29 par introspection des méthodes
  publiques (1 test, suite **575** verte). À rouvrir seulement si parallélisme
  multi-thread réel mesuré.
- 2026-10-21 : **S9 implémenté**. `tests/test_targets_s9.py` (4 tests d'intégration
  HostSim) : génération, update en cours d'action, purge entre scan et click,
  rejet partiel producteur. Couverture T1-T34 consolidée ; suite **579** verte.
  Le pilote réel CondiFlow reste R1 (smoke externe), rien n'est promis au-delà
  de la surface testée.
- 2026-10-29 : **T30-T32 durcis**. Constat : le scan T30 ne cherchait que les
  5 jetons du registre, ce qui a laissé passer des règles prescriptives à
  vocabulaire computer-use (grille, ruban, zoom, glisser) dans le Planner.
  `tests/test_targets_prompt_surface.py` ajoute 5 tests : vocabulaire du
  mécanisme cible absent sur **toutes** les entités × 3 langues, « absent =
  off » byte-identique, absence d'internes du registre capacité active,
  budget mesuré par entité, contrat invalide ≡ sans contrat. Le Planner perd
  une ligne de règle redondante avec `plan_grammar.md` (surface 359 → 304
  tokens). Suite **686** verte. Rien de runtime modifié.
- 2026-10-29 : **Traçabilité T1-T34**. Constat : 20 IDs seulement apparaissaient
  dans un nom de test ; « T1-T34 exécutés » (S9) n'était pas auditable.
  `tests/test_targets_traceability.py` porte la table `MATRIX` et vérifie que
  les 34 IDs sont déclarés une fois et que chaque fonction citée existe.
  §5.1 ajouté (index de lecture par fichier). Suite **689** verte.
- 2026-10-31 : **P0-P5 (plan ultime)**. P0 : `any` accepte `result=false`,
  `null`/absent = `"true"` rigide, warning `expected_warnings` (jamais un
  refus). P1 : descriptions de champs + message de rejet groupé (< 1 200
  chars). P2 : rendu du world state priorisé, champs entiers, coupe au dernier
  séparateur. P3 : badge `found` sur `result`, tokens mission/session, event
  `world_snapshot`. P4 : double leçon supprimée, i18n réelle (extraction AST,
  `.po`/`.mo` sans dépendance). P5 : routeur de scope en langage naturel
  (`investigate`, flag off par défaut). Suite **740** verte.
- 2026-11-06 : **Lots 1-4 — ciblage visible, P0/P1, `disclosure`, docs**.
  Lot 1 : `core/targeting.py` (`ObservedWorld` = brut + médias + candidates +
  attributs + grille + historique de sondes) ; le sélecteur n'est plus appelé
  avec la PD générique, la boucle « anchor or disclose » résout elle-même la
  demande d'inspection. Lot 2 : les 5 retours `SolverResult(FAILED)` portent
  les compteurs matériels ; signature d'échec structurée
  `classe|outil|origine|prose[:120]|digest` ; taxonomie d'origine ; santé
  outil et stérilité ne comptent que `TOOL_ERROR`/`UNKNOWN` ; la convergence a
  son propre disjoncteur après le recul. Lot 3 : `fields.attributes` +
  `target_contract.disclosure` (grille, ancres, sondes bornées à 2),
  cellules finales seulement via un consommateur `scope: cell` + `raw: allow`,
  médias transmis au sélecteur par le canal média, refus honnêtes
  (`TARGET_ANCHOR_UNSUPPORTED`, `TARGET_PROBE_LOOP`). Lot 4 : HOST_CONTRACT
  FR/EN, schéma et exemple de manifeste synchronisés. 8 tests dédiés
  (`tests/test_target_disclosure_flow.py`) ; suite **804** verte. Aucun
  commit/push.
- 2026-11-06 : **Audit d'agnosticisme + refonte générique**. Constat : le cœur
  contenait encore `SUBJECT_KINDS`/`PROBE_KINDS`, `grid_path`,
  `anchor.candidate/cell`, `target_kind candidate|cell`, `allow_cell`,
  `_consumer_spec_for_cell` et `scope == "cell"`. Remplacés par :
  `views[]` (vues nommées), `anchors[]` (`kind` libre → `param`), `probe.kind`
  libre, `target_kind` `local|anchor`, `anchor_kind`. Une ancre finale est
  acceptée si l'hôte l'a déclarée et qu'un consommateur `raw: allow` la reçoit
  (résolution par `anchors`, puis `scope`, puis `param` — aucun littéral).
  Nouveaux bancs `tests/test_target_hosts_agnostic.py` : calculatrice
  déterministe sans perception, robot, IDE. Suite **808** verte.
- 2026-11-06 : **Conformance manifeste réel CondiFlow (AutoCUse)**. L'hôte a
  livré `views`/`anchors`/`fields.attributes` et les consommateurs bruts dans
  les blocs réels `vision`/`target` (`rpahostprovider.h`). Les deux contrats
  passent le parseur v1 sans erreur ni warning. Nouveau banc
  `tests/test_target_condiflow_conformance.py` (6 tests) sur le manifeste
  exact : candidate locale → `preset` résolu `t1` → `mouse` reçoit `ui:<id>` ;
  ancre `cell=B3` → `target cell=B3` ; sondes `focus_candidate` (sujet
  `local`) et `inspect_cell` (sujet `cell`) ; `keyboard` `raw: allow`
  (`none` littéral en appel direct, nom local résolu). Correctif générique :
  un `refresh` du monde efface une vue absente du nouveau payload (pas de
  grille périmée). Suite **815** verte, i18n **640** chaînes.

---

## 12. Monde en targets (P2)

- `get_world_state` peut déclarer `produces: targets` : la photo est
  **projetée** (`register=False`) mais **non inscrite** au registre. Pour agir,
  une étape de perception/target normale doit produire la cible. Décision
  détaillée : `docs/HOST_CONTRACT.md` §3g.1.
- `world_snapshot.max_chars` reste déclaré par l'hôte (max 4000). Le rendu
  (`core/alignment.py::render_world_snapshot`) ne coupe jamais un mot :
  priorité summary > focus > windows > targets, champs entiers omis et
  comptés (`+N champs omis`), listes capées à 10 entrées avec compteur.
- `window_control(target, action)` est un outil hôte **optionnel**, documenté
  uniquement (`HOST_CONTRACT.md` §3g.2) : aucun code spécifique au cerveau.

## 13. Routeur de scope (P5, DETTE-3)

- `core/scope_router.py` : `ScopeRouter.candidates()` expose une vue courte
  (`data_type` + `get_scope_description()`) ; un méta-LLM renvoie
  `{providers, reason}` ; `answer()` interroge les providers retenus via
  `DataProvider.answer(question)` (défaut `None`).
- Outil `investigate({question})` : **langage naturel uniquement**, aucune
  action dispatchée. Refus explicite
  (`Aucun scope ne peut traiter cette demande : <question>`) : ce n'est pas un
  rejet de plan.
- Flag `runtime_state.enable_scope_router`, défaut **off**. Off : PD
  byte-identique, `investigate` ni connu ni visible ni exécutable. On : la PD
  complète est remplacée par **une ligne** (`Scopes disponibles : …`) et la
  boucle de découverte est court-circuitée ; l'entité passe par `investigate`.
- Activation après R1/R2 (mesure du delta tokens/appel et du taux de refus).

## 14. Ciblage visible et inspection (2026-11-06)

Le sélecteur ne devine plus : il **voit** un monde observé et peut demander
une inspection déclarée par l'hôte.

- `core/targeting.py::ObservedWorld` agrège : payload brut, médias du canal
  `returns`, candidates projetées (`key`, `description`, `scope`,
  `attributes`), vues nommées lues via `disclosure.views[]`, historique des
  sondes.
- `TargetDecision` (`BaseDiscoverySchema`) porte `target_key`, `target_kind`
  (`local`|`anchor`), `anchor_kind`, `no_target`, `reason` et
  `discovery_request`. `local` = clé du registre ; `anchor` = valeur brute
  d'un type **déclaré par l'hôte**. Aucun `cell`/`grid`/`preset` en dur.
- Boucle bornée « anchor or disclose » : au plus **2 sondes**. Une sonde est
  identifiée par `id` + sujet ; la même paire redemandée est refusée
  (`TARGET_PROBE_LOOP`). Le LLM ne fournit jamais d'outil ni d'argument fixe.
- `fields.attributes` expose au sélecteur des champs libres déclarés
  (`role`, `rect`, `cells`, `angle`, ...). Repli générique limité aux champs
  projetés : aucune référence brute ne fuit.
- `disclosure` (version 1, optionnel) : `views[]` (`name`, `path`),
  `anchors[]` (`kind`, `param`), `probes[]` (`id`, `kind` **libre**, `tool`,
  `args`, `hint_param`, `subject`, `produces`). Les sondes s'exécutent par
  `tools_mgr.execute_tool` avec les arguments déclarés.
- Sonde `subject.kind = "local"` : nom local si le paramètre est un
  consommateur déclaré (egress résout), sinon référence brute. Tout autre
  `kind` est une ancre déclarée et exige `raw: allow`.
- Cible finale ancre : le `kind` doit être déclaré (`anchors[]` ou consommateur
  `raw: allow` dont le scope/param vaut ce kind) ; sinon refus honnête
  `TARGET_ANCHOR_UNSUPPORTED`, aucun producteur appelé.
- Le contrat reste `version: 1` : tous ces blocs sont optionnels et non
  cassants. Sans contrat ou sans `disclosure`, le comportement historique est
  inchangé.
- Preuve multi-hôtes : `tests/test_target_hosts_agnostic.py` simule une
  calculatrice déterministe **sans aucun outil de perception**, un robot
  (articulations + attributs) et un IDE (ancre `file` puis cible locale).
- Conformance hôte réel (06/11/2026) : `tests/test_target_condiflow_conformance.py`
  rejoue sur les blocs exacts `vision`/`target` de CondiFlow les flux
  `local`, `anchor` (`cell`), les sondes `focus_candidate`/`inspect_cell` et
  le `keyboard` `raw: allow`.


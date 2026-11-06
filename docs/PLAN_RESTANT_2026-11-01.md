# Plan restant — 2026-11-01

**Statut** : diagnostic clos sur les logs CondiFlow du 2026-10-31, correctifs
harnais F1–F7 **implémentés et testés**, refonte des prompts laissée à
Christian, P5.5 (activation du flag) à faire.
**Suite de référence** : 740 tests verts avant ce lot ; 766 après (+26).
**Aucun commit/push** n'est fait sans GO explicite.

Ce document remplace le « Next Move » de la session précédente. Il corrige
aussi une conclusion erronée du diagnostic précédent (§0), preuve à l'appui.

---

## 0. Correction du diagnostic précédent (important)

Le diagnostic précédent affirmait :

> Cause racine n°1 (harnais, Gemini) : `Plan.model_json_schema()` contient
> `$defs`/`$ref` ; `types.Schema.model_validate(sd)` échoue, donc le modèle ne
> voit pas les propriétés de `PlanStep` (dont `output_target_name`) et
> hallucine `status`/`result_context`.

**C'est faux sur les deux points. Ne pas le réintroduire.**

### 0.1 Le schéma est bien résolu et bien envoyé

`GeminiProvider.generate_structured_output` (`providers/gemini_provider.py:282-300`)
passe un `dict` brut à `GenerateContentConfig(response_schema=...)`. Ce dict
n'est **pas** validé à la construction ; il est transformé au moment de la
requête par `google/genai/_transformers.py:869-877` :

```python
def t_schema(client, origin):
  if isinstance(origin, dict) and _is_type_dict_str_any(origin):
    process_schema(origin, client)          # inline les $ref, absorbe $defs
    return types.Schema.model_validate(origin)
```

Mesure réelle (`t_schema(None, Plan.model_json_schema())`) :

- `t_schema OK`
- `steps.items.properties` = **18 propriétés**
- `output_target_name` présent, avec sa description complète
- `status` / `result_context` **absents** (correct)

Donc le modèle **voit** `output_target_name`. Le schéma n'est pas la cause.

### 0.2 `status` / `result_context` ne sont pas inventés par le modèle

`core/plan_models.py:233-234` :

```python
status: SkipJsonSchema[ExecutionStatus] = ExecutionStatus.PENDING
result_context: SkipJsonSchema[str] = ""
```

`SkipJsonSchema` les retire du **schéma**, pas du **modèle**. Ils ont une
valeur par défaut, donc `proposed_plan.model_dump_json(indent=2)`
(`Planner.propose_plan`, `core/planner.py:309`) les écrit **toujours** dans le
log `[Planner] Plan généré`. Leur présence dans les logs ne prouve rien sur le
modèle : c'est de la sérialisation Pydantic.

> Leçon de méthode : un log de `model_dump` n'est pas une preuve de sortie
> modèle. Pour ça, il faut l'événement `llm_call` (texte brut de la réponse).

---

## 1. Faits vérifiés (logs + code)

| # | Fait | Preuve |
|---|---|---|
| 1 | Le modèle n'a **jamais** rempli `output_target_name` sur les 3 missions (YouTube ×2, Notepad) | `prompts_log/*_planner.md` + plans loggés : `"output_target_name": null` partout |
| 2 | Mission 1 : le modèle crée une cible avec `target` (`{"cell":"A1","label":"navigateur"}`) puis consomme `$@_target_step_3` | plan mission 1, step_3/step_4 |
| 3 | Mission 2 : le modèle consomme `$@_data_step_8.target` / `$@_data_step_13.target` | rejet `plan_rejected_validation` 17:25 |
| 4 | Aucun canal `target_<step>` n'existe : seuls `bool_<id>` / `data_<id>` sont auto-publiés | `Executor` (`bool_<id>`/`data_<id>`, `core/executor.py:229-230`), `PlanValidator.find_unresolved_plan_refs` |
| 5 | L'hôte déclare bien `produces` pour `target` (`data.target`, `mode: single`) | `CondiFlow/Agentic/rpahostprovider.h:418-425` |
| 6 | Le Planner **accepte** `$@_step_7` nu, le Supervisor **refuse** | `Planner._check_var` vs `PlanValidator.find_unresolved_plan_refs` |
| 7 | Le retry affiche « Aucun plan généré » alors que le plan rejeté existe | `Solver.run`, branches `except ValidationError` / `except ValueError` ; `proposed_plan` local reste `None` |
| 8 | Le plan rejeté **est** stocké dans la tentative (mais pas dans le prompt) | `Solver._record_plan_rejection` lit `planner._last_proposed_plan` |
| 9 | `_validate_plan` avale silencieusement un `tool_args_json` non-JSON | `Planner._validate_plan` (`except: pass`) |
| 10 | Les `id` d'étapes dupliqués ne sont détectés nulle part | `PlanValidator.validate` : aucun contrôle d'unicité |
| 11 | Mission 3 : `tool_args_json` pollué (`} formulation_args:{} waiting_ms:1000`), `id: "step_1"` ×3 | log 17:30:42, plan `046a2a3b` |
| 12 | `_summarize_plan` n'affiche pas `output_target_name` | `Solver._summarize_plan` |
| 13 | Au moment du diagnostic (31/10) : 4 épisodes, **0 leçon**, 0 skill | `memory.db` : `lessons=0`, `skills=0`, `skill_versions=0` |
| 14 | Le learner ignore `outcome="rejected"` et abandonne si `target_entity is None` | `Analyzer._traverse_tree`, `Analyzer._generate_avoid_lesson` |
| 15 | L'hôte n'a aucune faute sur ces 3 missions (contrats complets, `success: true`) | `user_logs.txt`, `prompts_log/*vars.txt` |
| 16 | Après F5 (mission `64f85627`, 01/11 05:02) : **1 leçon** réellement produite | leçon #487, `entity_type=Planner`, `polarity=avoid`, source `64f85627` ; `core/learner.py` modifié 04:52:29 < création 05:02:31 |

**Verdict** : la cause dominante est le **modèle** (`gemini-3.5-flash-lite`,
`gemini-3.5-flash`, `gemini-2.5-flash` : tous dégradent de la même façon), et
la **rédaction du prompt** qui rend `output_target_name` non évident. Le
harnais n'a pas causé l'échec, mais il a **mal feedbacké** les rejets et n'a
jamais produit de leçon.

---

## 2. Correctifs harnais (F1 → F7)

Règle : **tests rouges d'abord**, puis implémentation. Aucune réparation
automatique d'un plan (§ doctrine P1.1 refusée). Aucune suppression de cible.

### F1 — Afficher le plan rejeté dans le feedback de retry

- Fichier : `core/solver.py`, branches `except ValidationError` / `except
  ValueError` de `Solver.run` (helper `_resolve_rejected_plan`).
- Changement : remplacer `if proposed_plan is not None:` par une résolution
  qui retombe sur `getattr(self.planner, "_last_proposed_plan", None)`.
- Effet : le modèle voit la structure qu'il vient d'écrire au lieu de
  « Aucun plan généré (erreur de validation) ».
- Test : `tests/test_plan_rejection_feedback.py`
  - plan valide structurellement mais rejeté par `_validate_plan` →
    le feedback contient `step_1` et `tool=`.
  - plan rejeté par Pydantic (aucun plan) → feedback contient toujours
    « Aucun plan généré ».

### F2 — Aligner le Planner sur la règle de résolution du Supervisor

- Fichier : `core/planner.py`, `Planner._validate_plan` → `_check_var`.
- Changement :
  1. `$@_<id>` nu (sans préfixe `bool_`/`data_`) n'est **plus** considéré
     comme disponible via `f"bool_{clean_root}" in known`. Seuls
     `bool_<id>`, `data_<id>`, `output_variable_name` et
     `output_target_name` sont des noms valides.
  2. Ne plus émettre de piste « Vouliez-vous dire » pour une référence qui
     alimente un paramètre consommateur de cible : le message
     « Cibles non déclarées » est déjà le bon. Sinon on envoie le modèle
     vers `$@_data_step_N`, qui est le mauvais canal.
- Ordre des messages (non évident, à ne pas « simplifier ») : l'étape
  `direct_answer` est testée **avant** l'absence de préfixe, parce que le
  message de préfixe suggère `$@_data_<id>` — un canal qui n'existe pas pour
  une réponse directe. De même l'étape future passe avant, car la causalité
  est l'erreur dominante. Le message de préfixe ne sort donc que pour une
  étape réellement passée et productrice.
- Effet : un seul mur de validation, un seul message, pas de tentative
  brûlée sur un plan que le Supervisor refusera de toute façon.
- Tests :
  - `tests/test_planner_bare_step_ref.py` : `$@_step_7` en argument exécutable
    → `_validate_plan` renvoie `False`.
  - `tests/test_planner_unknown_target_hint.py` (existant) : doit rester vert,
    et ne doit plus contenir `Vouliez-vous dire` pour une cible.

### F3 — Rejeter les `tool_args_json` illisibles et les `id` dupliqués

- Fichiers : `Planner._validate_plan` (`core/planner.py`),
  `PlanValidator.find_malformed_step_args` / `find_duplicate_step_ids`
  (`core/plan_validator.py`).
- Changement :
  1. `_validate_plan` : sur `json.JSONDecodeError`, ajouter une erreur
     `args illisibles étape:<id> : <texte fautif tronqué à 80 chars>` au lieu
     de `pass`.
  2. `find_malformed_step_args` : dédupliquer les ids retournés (un id répété
     ne doit pas produire N fois la même ligne).
  3. Nouvelle fonction pure `find_duplicate_step_ids(plan)` dans
     `core/plan_validator.py`, branchée dans `Planner._validate_plan` **et**
     dans `PlanValidator.validate` (gate déterministe).
- Effet : mission 3 aurait été rejetée avec le texte exact fautif, et les
  `id: "step_1"` ×3 n'auraient pas silencieusement écrasé le registre.
- Tests : `tests/test_plan_args_and_ids.py`
  - `{"mode":"key"} waiting_ms:1000` → erreur contenant `waiting_ms:1000`.
  - deux `step_1` → erreur contenant `step_1`.
  - un seul id fautif dupliqué → une seule ligne.

### F4 — Le résumé de plan doit montrer la cible

- Fichier : `Solver._summarize_plan` (`core/solver.py`).
- Changement : ajouter `target=<output_target_name>` à chaque ligne **qui en
  a un** (pas de `target=None` : bruit inutile, et
  `PlanValidator._summarize_plan_for_prompt` suit déjà cette règle).
- Test : `tests/test_solver_summary_target.py` :
  un step avec `output_target_name="rect_tool"` doit apparaître ; un step
  sans cible ne doit produire aucun `target=`.

### F5 — Le learner doit produire des leçons

- Fichier : `Analyzer._traverse_tree`, `Analyzer._analyze_attempt`,
  `Analyzer._generate_avoid_lesson` (`core/learner.py`).
- Changement :
  1. `_traverse_tree` : considérer `outcome in ("failed", "rejected")`
     (les rejets pré-exécution sont des échecs de plan, donc du Planner).
  2. `_generate_avoid_lesson` : `entity_type = attempt.target_entity or
     "Planner"` au lieu de `if not entity_type: return`.
- Effet : la mission Notepad (`convergence_failure`, `target_entity=None`) et
  les rejets `plan_rejected_validation` produisent enfin une leçon.
- Tests : `tests/test_learner_rejected_attempts.py`
  - arbre avec 1 `rejected` (`target_entity="Planner"`) → `upsert_lesson`
    appelé 1 fois.
  - arbre avec 1 `failed` `target_entity=None` → `entity_type == "Planner"`.
  - arbre avec `USER_CANCELLED` → 0 leçon.

### F6 — Une seule construction du feedback de retry

- Constat : le bloc `[⚠️ REJET DU PLAN PRÉCÉDENT]` est écrit **deux fois à
  l'identique** dans les deux branches `except` de `Solver.run`. Toute
  correction future (F1, F4) doit être faite deux fois, donc sera oubliée
  une fois.
- Changement : extraire `Solver._build_plan_retry_feedback(error_msg,
  rejected_plan_summary) -> str` et l'appeler dans les deux branches.
- Test : `tests/test_plan_retry_feedback_single_source.py` — les deux
  branches produisent exactement la même chaîne pour une même erreur.
- Note : `PlanValidator._summarize_plan_for_prompt` reste tel quel (résumé
  riche destiné au juge, déjà couvert par
  `tests/test_plan_summary_target.py`). On ne fusionne pas les deux
  résumés : ils n'ont pas le même destinataire.

### F7 — Vérrouiller le schéma Gemini (garde, pas correctif)

- Fichier : `GeminiProvider.generate_structured_output`
  (`providers/gemini_provider.py`).
- Changement : test de non-régression qui vérifie que
  `t_schema(None, Plan.model_json_schema())` expose bien les 18 propriétés et
  `output_target_name`. Aucun changement de code produit : c'est pour empêcher
  qu'un futur refactor casse silencieusement le schéma.
- Test : `tests/test_gemini_schema_exposure.py` (skip si `google.genai`
  absent).

---

## 3. Prompts — à retravailler par Christian (pas édités ici)

Défauts constatés, à corriger en priorité :

### 3.1 `target` : « renvoie une cible nommée (data.target) »

Le contrat hôte (`rpahostprovider.h`) dit littéralement `data.target`, et
l'argument s'appelle `label`. Le modèle en déduit deux choses fausses :
`$@_data_step_N.target` et `$@_target_step_N`. Les deux sont refusés.

Ce qu'il faut rendre évident, en langage naturel :

- `data.target` est **la valeur de retour** de l'outil, pas un nom de variable
  du plan.
- Un résultat n'est pas adressable par chemin : `$@_data_step_N.target`
  n'existe pas.
- Pour réutiliser une cible, **le seul** mécanisme est `output_target_name`
  sur l'étape productrice, puis `$@_<ce nom>` chez le consommateur.
- `label` est un libellé lisible, pas un nom de variable.

### 3.2 `## INTERDICTION ÉCRITE PAR LE HARNAIS` (`prompts/base/planner.md:76-81`)

Le bloc dit « JAMAIS dans une abstract_task », « boucle infinie garantie »,
« moins de ~10 étapes = tool_call directs ». Résultat : sur 17 et 19 étapes,
le modèle n'a **jamais** produit d'`abstract_task`. L'intuition de Christian
est confirmée par les logs.

À réécrire en **rôle et cas d'usage** (pas de « si/sinon ») : ce qu'un
sous-agent apporte, ce qu'il coûte, quand la délégation a du sens. Le harnais
garde son gate déterministe (`detect_lazy_delegation_and_tree_recursion`) :
le prompt n'a pas à jouer au garde-fou.

### 3.3 EXEMPLE 3 avec placeholders

`prompts/base/planner.md:135-136` utilise
`<OUTIL_PRODUCTEUR_DE_CIBLE>` / `<OUTIL_ACTION_SUR_CIBLE>`. Le modèle ne peut
pas le mapper sur `target` / `mouse`. Soit l'exemple utilise les noms réels
quand `targets_enabled`, soit il disparaît.

### 3.4 Contradiction 5–10 étapes vs plans de 17–19

`prompts/base/planner.md:96` (« 5 à 10 étapes typiquement ») contredit la
réalité observée. À trancher : soit c'est une contrainte, soit c'est un
conseil — mais pas les deux.

### 3.5 Rappel doctrine

- Aucun préfixe ni texte français codé en dur : tout passe par `_()` et le
  pipeline `.po`/`.mo` (`scripts/compile_i18n.py --check` : **576** chaînes
  cataloguées après ce lot, contre 568 avant).
- `expected_result` : `any` = « je ne juge pas, je branche dessus » ;
  `null`/absent = `"true"` rigide ; `false` reste un échec par défaut.
- Une sortie non consommée est un **warning**, jamais un refus.

---

## 4. Observabilité et leçons

### 4.1 Ce qui est déjà bon

- `tools/build_observability_report.py` n'a pas cassé le rattachement
  leçons/skills. Le rapport tourne : 4 missions, 0 leçon, 0 skill.
- `observability/events.jsonl` (1,6 Mo) contient bien les événements réels.
- `skill_lifecycle` avec `mission_id: null` est rattaché par `session_id` ;
  sans session il apparaît en orphelin visible (voulu).

### 4.2 Ce qui manque

- `sessions = 0` dans un rapport de test venait uniquement de
  `--events events.jsonl` (fichier absent). Le vrai chemin est
  `observability\events.jsonl`. **Ne pas conclure à un bug d'export.**
- **Vérifié le 02/11** : `tools/build_observability_report.py --db memory.db
  --events observability\events.jsonl` → « 6 mission(s), 1 leçon(s),
  1 session(s) ». Le chemin de bout en bout fonctionne.
- La leçon #487 (créée le 01/11 à 05:02:31, soit après l'édition de
  `core/learner.py` à 04:52:29) porte sur la mission `64f85627` :
  `entity_type=Planner`, `polarity=avoid`, et sa recommandation parle
  exactement du sujet F2/F4 (« déclarer les variables cible via
  `output_target_name` »). F5 est donc validé **sur données réelles**, pas
  seulement sur mock.

---

## 5. P5 — activation du routeur de scope

État initial : `core/scope_router.py` + `investigate` + prompts + 9 tests
écrits, mais **le flag n'était activable par aucun chemin**
(`enable_scope_router = False` posé en dur, rien ne le relisait).

Corrigé en P5.5 avec la convention existante du projet
(`MANAGENT_EVENTS_SINK`, `MANAGENT_RECORD_PROMPTS`) :

- [x] **P5.5** lire `MANAGENT_SCOPE_ROUTER` dans `RuntimeState.__init__`
  (`"1"` → `True`, tout le reste → `False`). Aucune modification de l'hôte.
  Test : `tests/test_scope_router_activation.py`.
- [ ] **P5.6** mesurer flag off (PD byte-identique) puis flag on (PD réduite à
  une ligne) sur une mission réelle. *(Bloqué : run réel non autorisé le
  02/11.)*
- [x] **P5.7** nom de l'outil `investigate` **conservé** (décision Christian,
  02/11). Aucun renommage : `tools/internal_tools.py`, `core/executor.py`,
  `core/llm.py`, `core/runtime_state.py`, `prompts/*/scope_router.md` et
  `tests/test_scope_router.py` sont déjà cohérents.

---

## 6. R1 / R2 / R3 (décisions Christian)

| # | Sujet | État | Décision attendue |
|---|---|---|---|
| R1 | CondiFlow commité localement `6ca30db`, non poussé | **tranché 02/11** | ne pas pousser (le commit local reste) |
| R2 | Flag `enable_scope_router` off → mission non exécutée | échec réel constaté | confirmer que c'est le comportement attendu (off = PD inchangée, pas « mission cassée ») |
| R3 | Reprise de la mission YouTube après F1–F5 | **reporté 02/11** | pas de run tant que la refonte des prompts (§3) n'est pas faite |

---

## 8. Lot « expert audit » — GO de Christian du 03/11, exécuté le 04/11

Audit froid de `AgentBridge/expert_proposal.txt` (v5, 4 experts, runs YouTube
31/10) confronté au code. Verdicts CONFIRMÉ/RÉFUTÉ/PARTIELLEMENT livrés le
03/11 (mémoire de session). GO explicite reçu : « Go Fix je valide… ton ordre
c'est mon ordre ». Ordre exécuté tel que proposé :

- [x] **P-A** — Supervisor (`core/plan_validator.py`) : message de rejet avec
  **texte fautif** (snippet ≤80 chars via `_malformed_step_args_details`),
  **conseils ciblés** (le suffixe générique « N'utilisez que les outils
  listés » ne s'affiche plus quand l'outil appelé était listé et que seul le
  JSON des args est fautif — Cause 3), **déduplication** des problèmes
  identiques ; et dédup des raisons identiques dans PAST FAILURES
  (`core/solver.py`).
- [x] **P-B (décision : option B, tolérance)** — nouveau parseur tolérant
  partagé `parse_tool_args_json` (`core/plan_models.py`) : chemins strict →
  fences de code → objet embarqué + traîne **inerte uniquement** (aucun
  `:`/`=`/accolade dans la traîne, sinon illisible — cas
  `waiting_ms:1000`, test de régression existant respecté) → virgule
  traînante → guillemets typographiques → échappements invalides (portage de
  `Executor._safe_json_loads`). Routé depuis : `PlanStep.get_parsed_args`,
  validator `perceive_understand`, `_parse_step_args` + les 4 sites
  `plan_validator`, gates, `planner.py` (×2), `orchestrator.py`,
  `executor._safe_json_loads` (contrat d'exception `JSONDecodeError`
  conservé). 20/20 cas du probe.
- [x] **P-C** — `PlanStep` : `tool_name` + `tool_args_json` déclarés **juste
  après `type`** + `description=` riche sur les deux. L'ordre du schéma
  envoyé change POUR TOUS LES PROVIDERS (gemini/anthropic/openai/deepseek/
  groq/openrouter construisent chacun leur schéma depuis le même modèle
  Pydantic — vérifié : `model_json_schema()` chez chaque provider). Aucun
  code n'itère sur l'ordre des champs.
- [x] **P-D** — prompts (décisions Christian : vrais noms injectés au rendu +
  include séparé) :
  - `planner.py` : résolution agnostique des **vrais noms** d'outils cibles
    depuis le manifeste (première cible « producteur [produces] » + premier
    « consommateur 'target' »), passés au template
    (`target_producer_tool` / `target_consumer_tool`) ; fallback = consigne
    d'indirection (style EXEMPLE 1) si manifeste muet ;
  - `planner.md` (base/en/fr) : EXEMPLE 3 sans placeholder figé
    (`<TARGET_PRODUCER_TOOL>`/`<TARGET_ACTION_TOOL>` bannis — source de
    l'invention `$@_data_step_8.target`) ; ligne CHECKLIST retravaillée pour
    inclure l'univers des noms de cible (sans le token `output_target_name`
    hors garde — test d'inertie T0 respecté) ;
  - checklist de `plan_grammar.md` extraite vers **`plan_checklist.md`**
    (base/en/fr) ; `skill_repair`/`skill_synthesis` incluent les deux, le
    planner n'a plus qu'une checklist (gain de tokens) ;
  - budget de surface cible T31 respecté (317 ≤ 320 tokens, bench
    `test_targets_prompt_surface`).
- [x] **P-E** — `PLANNER_PD_ENABLED` est devenu le **vrai gate** de
  l'entité : `with_discovery=enable_world_pd and PLANNER_PD_ENABLED`
  (`core/planner.py`). Fin de l'incohérence observée le 31/10 (injection
  `discovery_injection.md` 4,25 Ko sur 4 des 6 calls Plan avec le flag
  désactivé). Test world : contrat mis à jour (flag off → plus de PD ; flag
  on → comportement retry d'origine rétabli). R2/`MANAGENT_SCOPE_ROUTER`
  inchangé (global, env var).
- [x] **P-F** — audit doc avant contact : `data.target` n'existe AUCUNE part
  dans les docs CondiFlow (source de vérité propre) — uniquement dans le
  **code** (`rpahostprovider.h:397` description LLM + contrat `produces`
  l.418, payload interne `rpahosttools.cpp:2701`). Lettre conforme envoyée à
  la team hôte via `AgentBridge/brain_to_host.md` (conformité doc, pas
  d'invention, relecture des 5 descriptions d'outils, confirmation que
  `produces` ne fuit pas vers un prompt). Christian relaie le 03/11.

**Tests** : 768 verts (766 avant, +1 test monde P-E, +1 fallback EXEMPLE 3 ;
1 test réaligné au nouveau contrat EXEMPLE 3). Suite complète passée 2×.

**Décision mémorisée (Christian, 02/11)** : ManAgent est **AGNOSTIQUE** —
aucun exemple host-spécifique dans les prompts ; la place d'un mini-exemple
d'usage = la description de l'outil côté hôte, au coût prompt que cela
représente (à trancher le moment venu, ensemble).

**Anomalie à signaler (non bloquante)** : `prompts_log/` est pollué par les
tests pytest (fichiers 08xx écrits au 11/02 02:21 et à chaque suite) — les
dumps de tests ne doivent pas envahir le canal des dumps réels de runs.

**Reste ouvert** :
- [ ] run de validation réel — R3 levé (les points prompts §3 liés au lot
  P-D sont traités) : refaire les 3 runs YouTube du 31/10 en conditions
  égales ; R2 (flag off = PD inchangée) à confirmer à cette occasion ;
- [ ] réponse team hôte sur `brain_to_host.md` (aucun fix hôte de notre
  côté) ;
- [ ] commit/push : **aucun commit n'est fait** — GO de commit attendu.

---

## 7. Ordre d'exécution et critères de sortie

1. Écrire les tests de F1 → F7 (rouges). ✅
2. Implémenter F1 → F5, puis F6, puis F7 (garde). ✅
3. `.venv\Scripts\python.exe -m pytest -q` → **tous verts** : **766 passed**
   (115,8 s et 135,8 s sur deux runs ; test le plus lent : 5,9 s). Le run à
   388 s observé plus tôt venait de la charge machine, pas d'un test
   pathologique.
4. `.venv\Scripts\python.exe scripts\compile_i18n.py --check` → OK
   (576 chaînes).
5. ✅ `tools/build_observability_report.py --db memory.db --events
   observability\events.jsonl` → « 6 mission(s), 1 leçon(s), 1 session(s) ».
6. Attendre **GO** avant tout commit/push.

Critère d'acceptation global : sur un rejet de plan, le prompt de retry
contient (a) la raison exacte, (b) le plan rejeté avec `target=`, (c) aucun
« Vouliez-vous dire » qui pointe vers le mauvais canal. Et une mission
terminée produit au moins une leçon.

---

## 8. Explicitement hors périmètre

- ❌ Réparation automatique `label` → `output_target_name` (P1.1 refusée :
  le harnais contrôle, il ne répare pas).
- ❌ Suppression ou renommage des targets.
- ❌ `ask_scope` (P5 est un routeur NL + méta-LLM, pas une question).
- ❌ Modification de l'hôte CondiFlow : il est interrogé via
  `brain_to_host.md`.
- ❌ Retry identique sans progrès : chaque rejet doit changer le plan.

---

## 9. Journal d'implémentation — 2026-11-01

| Élément | Fichier(s) | Test(s) |
|---|---|---|
| F1 plan rejeté visible | `core/solver.py` (`_resolve_rejected_plan`), `core/planner.py` (reset `_last_proposed_plan`) | `tests/test_plan_rejection_feedback.py` |
| F2 une seule règle de refs | `core/planner.py` (`_check_var`, `unknown_hints`) | `tests/test_planner_bare_step_ref.py` |
| F3 args illisibles + ids dupliqués | `core/planner.py`, `core/plan_validator.py` (`find_duplicate_step_ids`, dédup `find_malformed_step_args`) | `tests/test_plan_args_and_ids.py` |
| F4 `target=` dans le résumé | `core/solver.py` (`_summarize_plan`) | `tests/test_solver_summary_target.py` |
| F5 leçons sur rejets + défaut Planner | `core/learner.py` | `tests/test_learner_rejected_attempts.py` |
| F6 feedback unique | `core/solver.py` (`_build_plan_retry_feedback`) | `tests/test_plan_retry_feedback_single_source.py` |
| F7 garde schéma Gemini | aucun code produit | `tests/test_gemini_schema_exposure.py` |
| P5.5 activation du routeur | `core/runtime_state.py` (`MANAGENT_SCOPE_ROUTER`) | `tests/test_scope_router_activation.py` |

Détail F2 : un `$@_step_N` nu est désormais refusé avec le **bon** canal
(`$@_data_step_N` / `$@_bool_step_N`) au lieu du message trompeur « n'a pas
produit de données valides ». La tolérance prose est conservée : un
`$@_step_N` cité dans une description reste un simple avertissement.

Détail F3 : `find_duplicate_step_ids` est branché dans **les deux** gates
(`Planner._validate_plan` et `PlanValidator.validate`), pour qu'aucun chemin
d'exécution ne puisse recevoir deux étapes de même id.

Détail i18n : `Solver._summarize_plan` contenait encore du français codé en
dur (`Structure du plan rejeté :`, `Variables utilisées : …`, `aucune`) et les
deux branches de retry `Aucun plan généré (…)`. Tout est passé par `_()` ;
catalogue régénéré par `scripts/compile_i18n.py --force` → 576 entrées.

### Validation sur données réelles (mission `64f85627`, 01/11 05:01)

L'arbre d'exécution stocké dans `memory.db` contient :

| Tentative | `outcome` | `failure_class` | `target_entity` |
|---|---|---|---|
| 1 | `failed` | `convergence_failure` | `None` |
| 2 | `rejected` | `plan_rejected_validation` | `Planner` |
| 3 | `rejected` | `plan_rejected_validation` | `Planner` |
| 4 | `rejected` | `plan_rejected_validation` | `Planner` |

- Tentative 2 : `$@_target_step_2` → « Cibles non déclarées » (cas F2 exact).
- Tentatives 3 et 4 : `failure_reason` **identiques au caractère près**
  (`args illisibles … {"cell": "H8", "label": "navigateur" }", "output…`) :
  c'est la preuve du retry identique que F1 corrige (le modèle ne voyait pas
  son plan rejeté).
- Leçon #487 produite à 05:02:31 avec `entity_type=Planner` et
  `polarity=avoid` → F5 validé hors mock.

### Décisions du 02/11

- Pas de commit ni de push ManAgent : tout reste en working tree jusqu'à la
  validation réelle.
- R1 : CondiFlow `6ca30db` **n'est pas poussé** (le commit local reste).
- R3 : pas de run de validation tant que la refonte des prompts (§3) n'est
  pas faite.
- P5.7 : le nom `investigate` est conservé tel quel.

Reste à faire : §5 P5.6 (bloqué par l'absence de run), §3 prompts (Christian),
§6 R2 (à confirmer au prochain run réel).

### Chantier suivant (annoté le 04/11, non commencé)

La refonte de la Progressive Disclosure et du routeur de scope est cadrée
dans `docs/PD_CHANTIER_2026-11-04.md`. Idée validée : remplacer la grosse
section PD auto-injectée par une **entité-pont sémantique** courte
(« tu as une mission ; si tu manques d'infos, fais un discovery request en
langage naturel sur un scope »), le routeur choisissant le DataProvider.
Ce chantier vient **après** les phases 0→5 du plan du 04/11.

### Phase 2b — flux composite presets (05/11)

La réponse hôte du 05/11 remplace l'ancre sémantique par des **presets
numérotés** (`t1..tN`, réinitialisés à chaque capture, TTL 120 s) + `vision
mode=inspect` + `target preset=tN`. Un plan ne peut donc plus écrire `tN` :
la lecture et l'ancrage doivent rester dans la même étape.

**Implémenté** :

- `perceive_understand` accepte `source_tool` **et** `target_producer`
  ensemble (flux composite). La source projette ses candidates, le méta-outil
  en choisit une par `target_description`, puis injecte son **nom local** dans
  le paramètre consommateur DÉCLARÉ du producteur (`target_args` explicite
  reste prioritaire). La cible finale est celle que le producteur vient de
  produire ; `output_target_name` la lie.
- Refus honnêtes : `TARGET_ANCHOR_UNSUPPORTED` si la source ne rend aucune
  candidate, si le producteur ne déclare aucun consommateur, ou si le scope de
  la candidate ne correspond pas. Aucun clic aveugle, aucune référence devinée.
- `perceive_action` profite du composite sans changement de signature.
- `plan_validator` : `target_args.<consommateur>` est un argument consommateur
  (alias `$@_...` obligatoire s'il est fourni) ; un flux composite exige que la
  source déclare `produces` **et** que le producteur déclare `consumes`.
- `executor._target_arg_sources` trace aussi `target_args.<param>=...`.
- Prompts planner (base/en/fr) : EXEMPLE 3 réécrit en composite compact,
  budget de surface T31 respecté (base 314, en 301, fr 314 tokens ≤ 320).
- Tests : `tests/test_presets_flow.py` (8 tests, `HostSim`) — candidates →
  sélection → `target preset=<local>` → `mouse target=<local>`, refus sans
  candidate / sans consommateur / mauvais scope, `target_args` préservé,
  plan composite valide/rejeté.
- i18n reconstruit (`scripts/compile_i18n.py`), 604 chaînes cataloguées.

**Reste à faire côté hôte** (lettre `AgentBridge/brain_to_host.md`) :

1. `vision` déclare `produces` sur `data.presets` (scope `ui_preset`,
   `fields.ref=id`, `fields.description=name`) et `data.elements` si `inspect`
   doit aussi ancrer.
2. `target` déclare `consumes: [{param:"preset", scope:"ui_preset",
   required:false, raw:"deny"}]` en plus de son `produces`.
3. Confirmer que `keyboard.target` n'est pas dans `required` du schéma (sinon
   `mode=text` sans cible serait refusé par notre prévalidation).

Tant que ces déclarations ne sont pas transmises, le retest YouTube reste
bloqué : le cerveau est agnostique, il ne peut pas deviner `preset`.

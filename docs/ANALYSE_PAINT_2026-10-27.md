# Analyse — missions Paint (session `{f052bc4e-…}`, 02:31 → 03:01)

Source de vérité : `CondiFlow/user_logs.txt` (456 lignes) + `prompts_log/` (prompts réellement rendus).
Objet : comprendre (1) les erreurs de validation d'un modèle moyen, (2) le faux `pass` de convergence de la mission 1, (3) le blocage matériel de la mission 2.

---

## 1. Résumé exécutif

1. **La validation n'est pas le goulot d'étranglement de cette session.** 6 plans générés, **5 acceptés**, **1 seul rejet déterministe** (alias de cible non déclarés), 1 troncature JSON. Le bruit perçu vient surtout de **20 rotations de clé sur 503** et de **9 appels `PlanValidationDecision`** (1 par validation, pas 1 par rejet).
2. **Le faux `pass` de la mission 1 n'est pas un bug de câblage.** Les `plan_gaps` étaient bien injectés dans le prompt de convergence (`prompts_log/0009_convergence.md.en.md`). Le juge LLM les a ignorés -> `is_convergent=true`, `reason="Mission accomplie."`.
3. **Trois couches indépendantes pouvaient rattraper la mission 1 ; aucune ne l'a fait** : le gate déterministe `is_mission_success` (3 outils réussis = succès), le juge de convergence (gaps ignorés), le présentateur (ne reçoit pas les gaps).
4. **Le blocage de la mission 2 est un bug de contrat hôte situé après la membrane cible**, pas le bug `TARGET_NOT_FOUND` corrigé. Le plan est correct, l'alias est publié, la cible est créée ; puis ManAgent envoie `target: {"id":…, "kind":"img"}` (objet) à `mouse`, dont le manifeste déclare `target` en `type: "string"`, et dont le gate d'entrée teste `.toString()` -> `TARGET_REQUIRED`.
5. **Le modèle moyen n'est pas la cause première.** Il a suivi une stratégie de faisabilité correcte, s'est corrigé après le rejet (3 plans suivants avec alias corrects), et a produit des plans exécutables. Il échoue là où le contrat est **abstrait, volumineux ou non illustré** : alias de cible, schéma à 20 champs, réponse finale écrite à l'avance.

---

## 2. Chiffres clés (extraits du log)

| Métrique | Valeur |
|---|---|
| Durée | 02:31 → 03:01 |
| Missions | 2 |
| `Plan` demandés au LLM | 11 requêtes (dont reprises 503) |
| Plans réellement générés | 6 |
| Plans acceptés | 5 |
| Rejets déterministes | **1** (02:55:16) |
| Échecs Pydantic (troncature) | **1** (02:56:39, `EOF while parsing a string at line 22 column 9114`) |
| `PlanValidationDecision` | 9 requêtes / 5 décisions |
| `FeasibilityDecision` | 8 requêtes |
| `ConvergenceDecision` | **1** (mission 1) -> `pass` |
| Code 503 + rotation de clé | **20** |
| `mission_retry` | 5 |
| `solver_recul` | 1 |
| Échecs `mouse` `TARGET_REQUIRED` | **3** (02:58:12, 02:59:24, 03:00:51) |
| `unused_declared_output` | 3 |
| `perception_unconsumed_warning` | 3 |

Conclusion chiffrée : sur ces deux missions, **1 plan sur 6** a été refusé par la validation. Le problème n'est donc pas « le modèle se fait rejeter en boucle », mais « le système accepte et déclare terminé ce qui n'est pas fait », plus un contrat de cible incohérent.

---

## 3. Chronologie utile

### Mission 1 — `ee848a24` (02:31)

| Heure | Fait |
|---|---|
| 02:31:47 | Plan 4 étapes : `keyboard lwin` -> `keyboard "paint"` -> `keyboard enter` -> `direct_answer` « Paint ouvert, prêt pour le dessin » |
| 02:31:49 | `plan_partial_accepted`, risk `low`, gaps : *« l'outil de dessin … n'est pas encore sélectionné »*, *« le carré complet n'est pas encore dessiné »* |
| 02:31:49 | `plan_validation_decision` : `is_valid=true`, `reason` « conforme, sûr et progresse logiquement vers l'ouverture de Paint » |
| 02:32:03 | Dernier `keyboard` réussi ; aucun pas de perception dans le plan |
| 02:32:05 | `ConvergenceDecision` -> `is_convergent=true`, `reason="Mission accomplie."` |
| 02:32:10 | Rapport présentateur : « ### Mission accomplie — Paint a été ouvert avec succès » |

Preuve du prompt de convergence (`prompts_log/0009_convergence.md.en.md`) :

- `Expected result` = **tout le goal**, y compris « dessiner un carré complet » ;
- `Plan gaps accepted before execution (must be verified)` = les **deux** lacunes ;
- `Actual result` = **uniquement** `[RÉPONSE] J'ai ouvert l'application Microsoft Paint…` ;
- **aucune preuve `[PERCEPTION]` ni `[MONDE]` du carré** ; le snapshot monde ne montre que « Paint ouvert, focus Pinceau ».

Le juge avait donc tout pour refuser, et a répondu `pass`.

### Mission 2 — `008bcdba` (02:52 → 03:01)

| Heure | Fait |
|---|---|
| 02:52:57 | Plan racine 7 étapes. `step_5` = `abstract_task` (sous-but ≠ goal racine, mais recouvre « sélectionner + tracer »), `step_7` = `direct_answer` **statique** « un carré net a été tracé ». `step_4`/`step_6` (`perceive_understand`) jamais consommés |
| 02:54:42 | Stratégie de faisabilité **correcte** : perceive -> target -> mouse -> target départ/arrivée -> drag -> perceive |
| 02:55:16 | Plan enfant 10 étapes : consomme `$@_target_rectangle`, `$@_target_canvas_start`, `$@_target_canvas_end` ; **tous les producteurs ont `output_target_name: null`** -> rejet déterministe (4 refs inconnues) |
| 02:56:39 | Deuxième tentative : JSON tronqué à 9 114 caractères -> retry Pydantic |
| 02:57:11 | Troisième plan : alias corrects (`target_rectangle`, `canvas_start`, `canvas_end`) -> accepté |
| 02:58:12 | `target` OK, puis `mouse` reçoit `{"action":"click","target":{"id":…,"kind":"img"}}` -> **TARGET_REQUIRED** |
| 02:58:33 | Recul `convergence_failure` -> nouvelle faisabilité -> nouveau plan |
| 02:59:19 | Plan correct (alias) -> accepté à 02:59:24 |
| 02:59:24 | `target` OK, puis **même** échec `mouse` |
| 03:00:33 | Replan racine (tentative 2/3), alias corrects -> accepté 03:00:42 |
| 03:00:51 | **Troisième** échec `mouse` identique |
| 03:01:06 | Nouvelle faisabilité ; arrêt manuel Double-Ctrl |

Le plan de 02:59:19 est la preuve que **la chaîne ManAgent est bonne** : alias déclaré, publié, résolu. Le seul maillon cassé est la **forme de la valeur envoyée à l'hôte**.

---

## 4. Pourquoi le modèle moyen « fait des erreurs de validation »

### 4.1 Le seul rejet : alias de cible non déclaré

Le modèle écrit :

```json
{ "id": "step_2", "tool_name": "target", "output_target_name": null,
  "tool_args_json": "{\"cell\":\"E2\",\"label\":\"outil_rectangle\"}" }
{ "id": "step_3", "tool_name": "mouse",
  "tool_args_json": "{\"action\":\"click\",\"target\":\"$@_target_rectangle\"}" }
```

Il **copie le `label`** (`outil_rectangle`) et **invente** un alias `target_rectangle` au lieu de le déclarer.

Causes mécaniques, par ordre d'impact :

1. **Aucun exemple concret du triplet cible** dans `prompts/base/planner.md`. Les deux exemples (lignes 114-129) montrent `perceive_understand` + `direct_answer`, jamais `target` -> `output_target_name` -> `mouse`. La règle est en prose abstraite (lignes 31-41), noyée dans 210 lignes et 26 outils.
2. **Ordre des champs du schéma** (`core/plan_models.py:99-111`) : `output_variable_name` puis `output_variable_desc` puis `output_target_name`. Le modèle remplit le réflexe « une étape produit une variable » et laisse `output_target_name` à `null`.
3. **Le diagnostic ne l'oriente pas.** `core/planner.py:417-422` dit « variable inconnue … n'a été produite par aucune étape antérieure » ; `core/plan_validator.py:528-530` suggère explicitement `output_variable_name` — **mauvais champ** pour un alias de cible. Le message correct serait : « si c'est une cible, déclare `output_target_name` sur le producteur ».
4. **Le plan est validé étape par étape, pas comme une chaîne cible.** Rien n'oblige le producteur `target` à porter un alias quand un consommateur utilise un `$@_…` libre.

Contre-preuve : après ce rejet, les **3 plans suivants** déclarent correctement les alias. Le modèle sait faire ; il ne le fait pas sans exemple ni contrainte de forme.

### 4.2 La troncature JSON : un problème de schéma, pas de modèle

`PlanStep` expose **20 propriétés** (`core/plan_models.py:84-230`), dont **5 champs internes sans description** :

- `status`, `result_context` (lignes 229-230) — état d'exécution, jamais une entrée ;
- `response_text`, `tool_name`, `tool_args_json` (225-227) — champs de rendu/transport.

Le modèle les émet (on les voit dans tous les plans du log), ce qui gonfle la sortie : **9 114 caractères pour ~2 étapes** avant coupure. La limite configurée est `LLM_STRUCTURED_MAX_OUTPUT_TOKENS = 8192` (`core/constants.py:212`), donc **ce n'est pas le plafond ManAgent** : la coupure vient du provider/du stop du modèle. Mais un schéma deux fois plus petit supprimerait la classe entière de retry.

### 4.3 Le bruit 503 est confondu avec des erreurs de validation

20 rotations de clé, 11 requêtes `Plan` pour 6 plans. Chaque 503 relance un `generate_structured` complet : le log donne l'impression de « plans en boucle » alors que le modèle n'a souvent rien produit.

### 4.4 Le plan racine « abstrait » n'est pas bloqué

`prompts/base/planner.md:76-81` interdit `abstract_task` pour le but courant. À 02:52:57 le modèle met `step_5` en `abstract_task` avec un sous-but reformulé (« Sélectionner l'outil Rectangle… »). La règle est **sémantique**, donc non vérifiable par le code ; le juge LLM l'a acceptée. Résultat : la mission descend d'un niveau, paie un solver enfant, et le plan racine ne contient aucune preuve du carré.

---

## 5. Pourquoi la mission 1 a passé (faux `pass`)

Quatre couches, quatre défaillances :

### 5.1 Gate déterministe `is_mission_success` — trop faible

`core/plan_models.py:272-288` : succès si `material_successes > 0`. Trois `keyboard` réussis -> `True`. Ce gate ne dit **rien** de la couverture du goal. Il a été conçu pour bloquer « direct_answer sans action », pas « action partielle ».

### 5.2 Juge de convergence — ambiguïté étape/mission + règle 4

`core/solver.py:1479-1636` construit :

- `step_description` = **dernière étape réelle** (« Appuyer sur Entrée ») ;
- `expected_result` = **goal complet** ;
- `plan_gaps` = les deux lacunes (correct).

Le prompt `prompts/base/convergence.md` demande « si le résultat d'une **étape** converge » (ligne 3), puis la règle 4 dit « ne sois pas trop strict, si l'essentiel est atteint, accepte » (ligne 9), et la règle 9 dit « chaque lacune doit être prouvée » (ligne 14). **Les règles 4 et 9 se contredisent** pour un modèle moyen : il a pris « Paint ouvert » comme « l'essentiel », et le `step_description` l'a ancré sur la réussite de la touche Entrée.

Aggravant : le snapshot monde montre « Paint ouvert, focus Pinceau » — le modèle a pu croire que la lacune « outil de dessin » était couverte, alors que le focus Pinceau ne prouve rien du carré.

### 5.3 Aucun garde-fou déterministe sur les gaps

`_verify_mission_convergence` lit `plan_gaps` (`core/solver.py:1553-1560`) puis **appelle directement le LLM** (1588-1615). Aucune règle du type :

> gaps non vides + aucune preuve de perception capturée après l'acceptation -> refus.

C'est pourtant **décidable** : dans la mission 1, `_perception_trail` est vide et l'évidence ne contient que `[RÉPONSE]`. Le `pass` était évitable sans LLM.

### 5.4 Le présentateur ne sait pas que le plan était partiel

`prompts/base/presentator_output.md` ne reçoit que `accumulated_response`, `detail_level`, `error_reason`, `final_context`, `goal`, `mission_status`, `session_mood`, `variable_registry` (`prompts_log/0011_…vars.txt`). **Aucune variable `plan_gaps` / `is_partial` / `convergence_reason`.** Il a donc produit « Mission accomplie » — le faux succès visible par l'utilisateur.

Bonus d'observabilité : `plan_validation_decision` n'expose pas `is_partial`/`partial_gaps` (`core/orchestrator.py:2270-2280`), seulement l'événement `plan_partial_accepted`. L'UI ne peut pas corréler « plan accepté » et « plan partiel ».

---

## 6. Pourquoi la mission 2 n'avance plus (chaîne cible)

### 6.1 Ce qui fonctionne

- `bind_target_alias` publie `variables[alias]["value"] = entry.local_name` (`core/target_ingress.py:74-111`) ;
- l'egress résout ce nom local via `TargetRegistry.resolve` (`core/target_contract.py:104-164`) ;
- le plan de 02:59:19 déclare et consomme correctement les alias ;
- `target` est bien exécuté et renvoie `data.target`.

### 6.2 Le maillon cassé : objet au lieu de chaîne

Log 02:59:24 / 03:00:51 :

```
mouse Args : {"action":"click","target":{"id":"6d70afbb-…","kind":"img"}}
```

Or le manifeste hôte déclare :

```cpp
// rpahostprovider.h:253
props["target"] = { {"type","string"}, {"description","REQUIS : nom local …"} };
// rpahostprovider.h:261
params["required"] = {"action","target"};
```

Et le gate d'entrée hôte :

```cpp
// rpahosttools.cpp:857-867
const bool hasTargetArg = args.contains("target")
    && !args.value("target").isNull()
    && !args.value("target").isUndefined()
    && !args.value("target").toString().trimmed().isEmpty();   // <-- objet -> ""
if (hasTargetArg) {
    if (!s_parseTargetRef(args.value("target"), …)) { … }
    …
} else {
    // rpahosttools.cpp:944-951
    "Action refusee : une cible nommee est obligatoire. …"
    error_reason = "TARGET_REQUIRED";
}
```

Le parseur hôte **sait** lire un objet (`rpahosttools.cpp:274-320` : `{kind,id}`, JSON-string, ou `kind:id`). C'est donc uniquement le **gate `hasTargetArg`** qui rejette la forme objet, avant le parseur. Incohérence interne de l'hôte :

- l'outil `target` teste seulement `isNull()/isUndefined()` (`rpahosttools.cpp:2447-2448`) -> accepte l'objet ;
- `mouse` et `keyboard` testent `.toString()` (`857-859`, `1253-1255`) -> rejettent l'objet.

### 6.3 Pourquoi ManAgent envoie un objet

`resolve_consumer_arguments` fait `resolved[param] = ref.value` (`core/target_contract.py:163`). `ref.value` est la référence brute de l'hôte, `{kind, id}` (`agenttargetstore.cpp:155-161`). La membrane est cohérente avec elle-même, mais **pas avec le type déclaré dans le manifeste** (`string`).

### 6.4 Conséquence : 3 cycles de replan pour rien

À chaque `TARGET_REQUIRED`, le solver classe l'échec en `EXECUTION_FAILURE`, fait un `recul` (`convergence_failure`), relance faisabilité + planner + validation. `is_nonretryable_contract_failure` (`core/plan_models.py:596-604`) ne couvre que `[pre-dispatch] + unresolved target reference`. Les refus hôte déterministes (`TARGET_REQUIRED`, `TARGET_SCHEMA_INVALID`) ne sont pas couverts -> **3 plans corrects jetés**, 3 `target` recréés, mission arrêtée à la main.

---

## 7. Audit des prompts, entité par entité

| Prompt | Taille | Rôle | Faiblesse constatée | Preuve |
|---|---:|---|---|---|
| `orchestrator.md` | 11 968 | Routage + `refined_goal` | « Dis QUOI, pas COMMENT » mais le goal mission 1 contient déjà « sélectionner l'outil … puis dessiner » ; 9 appels par mission pour superviser les `tool.result` | log 02:31:47, 9× `OrchestratorDecision` |
| `feasibility.md` | 2 519 | Faisabilité + stratégie | Bonne : stratégie correcte produite ; mais **avis seulement**, le planner peut l'ignorer (il l'a fait à 02:55:16) | log 02:54:42 |
| `planner.md` | 8 358 | Génération du plan | Pas d'exemple cible ; `output_target_name` noyé ; 20 champs ; réponse finale statique | plans 02:52:57, 02:55:16 |
| `plan_grammar.md` | 3 301 | Grammaire | Règles cible correctes mais abstraites ; checklist sans item cible | lignes 13-18, 35-41 |
| `plan_validation.md` | 6 266 | Juge de plan | `is_partial` 100 % déclaratif ; le résumé du plan **cache `output_target_name`** ; pas de contrôle « la réponse finale consomme la vérification » | `plan_validator.py:1242-1289` |
| `convergence.md` | 4 186 | Juge final | Mélange étape/mission ; règles 4 et 9 contradictoires ; pas de porte déterministe | `prompts_log/0009` |
| `presentator_output.md` | 6 882 | Rapport final | Ne reçoit **ni gaps ni raison de convergence** | vars `0011` |
| `mission_compactor.md` | 2 110 | Conseils | Conseils 662/942 chars, non décisifs | log 02:52:25 |

### 7.1 Le problème de fond : la réponse finale est écrite à l'avance

Dans les deux missions, `direct_answer.response_text` est une **affirmation statique** :

- mission 1 : « J'ai ouvert Paint… » (pas de vérification du carré) ;
- mission 2 (02:52:57) : « …un carré net a été tracé » alors que `step_6` est une vérification **non consommée** ;
- mission 2 (03:00:33) : « …un carré a été tracé avec succès » alors que `step_9` (`is_crucial`) est la vérification.

Aucune règle ne demande que la réponse finale **dépende** du résultat de vérification (`$@_data_step_N` ou branche `execute_if`). Le plan peut donc « réussir » en affichant un succès pré-écrit, même si la perception a dit « non ». C'est le vrai vecteur du faux succès, en amont de la convergence.

---

## 8. Verdict : qui est responsable de quoi

### ManAgent

1. **Convergence non fail-closed** sur les gaps (mission 1). P0.
2. **Prompt de convergence ambigu** étape vs mission, règles 4/9 contradictoires. P0.
3. **Présentateur aveugle** aux gaps et à la raison de convergence. P0.
4. **Egress cible non conforme au type déclaré** (`string` -> objet). P0 (débloque la mission 2 sans toucher l'hôte).
5. **Schéma `Plan` gonflé** de 5 champs internes -> troncature. P1.
6. **Diagnostic « variable inconnue »** qui suggère le mauvais champ. P1.
7. **`plan_summary` du juge** sans `output_target_name`. P1.
8. **Classement des échecs de contrat hôte** en erreurs rejouables -> 3 replans inutiles. P2.
9. **Événement `plan_validation_decision`** sans `is_partial`/gaps. P2.

### Hôte (CondiFlow)

1. **`hasTargetArg` incohérent** entre `target` (objet OK) et `mouse`/`keyboard` (objet rejeté), alors que `s_parseTargetRef` accepte l'objet. Correctif ~2 lignes (`isObject()`), à signaler à l'équipe hôte.
2. **Manifeste incohérent** : `target` déclaré `string` alors que le protocole transporte `{kind,id}`. À trancher : soit le schéma devient `oneOf[string, object]`, soit le runtime sérialise.
3. **Message d'erreur trompeur** : « rappelez mouse avec target=<nom local> » alors que l'hôte reçoit une référence brute ; il fait croire à un problème de plan.
4. **`TARGET_REQUIRED`** n'est pas dans les codes fermés documentés (à vérifier côté `HOST_CONTRACT`).

### Modèle moyen

- Suit bien les **exemples concrets**, mal les **règles abstraites**.
- Sature sur un schéma de 20 champs et 26 outils.
- Satisfice sur un jugement « l'essentiel est atteint » quand le prompt l'y autorise.
- **Il n'est pas la cause racine** : les 3 défaillances de la mission 1 sont des trous de contrat/garde-fou, pas des hallucinations.

---

## 9. Itérations proposées (ordre de valeur / coût)

### P0 — débloquer et arrêter les faux succès (sans architecture)

1. **Egress cible conforme au schéma** : dans `resolve_consumer_arguments`, si `parameters_schema.properties[param].type == "string"` et `ref.value` est un objet `{kind,id}`, émettre `"<kind>:<id>"` (accepté par `s_parseTargetRef`). Test HostSim avec un stub `mouse` qui **reproduit le gate hôte** (objet -> `TARGET_REQUIRED`) pour que le banc ne soit plus complaisant.
2. **Porte déterministe de convergence** : si `plan_gaps` non vide **et** aucune preuve de perception dans `_perception_trail` depuis l'acceptation -> `False` avant l'appel LLM. Test unitaire : mission 1 rejouée -> refus.
3. **Prompt convergence** : phrase explicite « tu juges la MISSION, pas la dernière étape ; `step_description` n'est qu'un contexte » ; règle 4 limitée aux variantes de formulation, jamais aux lacunes ; règle 9 rendue prioritaire.
4. **Présentateur** : injecter `plan_gaps`, `is_partial`, `convergence_reason` ; interdiction de « mission accomplie » si gaps non vides.
5. **Événement** : ajouter `is_partial` et `partial_gaps` à `plan_validation_decision`.

### P1 — réduire les erreurs de validation à la source

6. **3e exemple planner** : `target {"cell":"E2","label":"…"}` + `output_target_name:"rect_tool"` -> `mouse {"action":"click","target":"$@_rect_tool"}`. + item de checklist.
7. **Message d'erreur ciblé** : si la ref inconnue commence par `$@_target_`/vient d'un consommateur cible, dire « déclare `output_target_name` sur le producteur » (et non `output_variable_name`).
8. **Schéma LLM séparé** (`PlanDraft`) : 6-8 champs par étape, sans `status`, `result_context`, `response_text`, `tool_name`, `tool_args_json` internes. Conversion vers `Plan`. Objectif : −50 % de sortie.
9. **Règle de réponse finale** : un `direct_answer` final doit consommer la vérification (`$@_data_step_N`) ou brancher dessus ; lint déterministe + avertissement.
10. **`plan_summary`** : afficher `target=…` pour que le juge voie la chaîne cible.

### P2 — robustesse / coût

11. **Classer `TARGET_REQUIRED`/`TARGET_SCHEMA_INVALID`** (dispatched) comme non rejouables : un seul essai, remontée d'erreur contrat, pas de cycle faisabilité+planner.
12. **Budget Progressive Disclosure** : 4,3 k chars pour le plan ; envisager un plafond plus bas pour les petits modèles.
13. **Observabilité** : compter les plans rejetés par cause (déterministe vs LLM vs Pydantic vs 503) pour arrêter de confondre les trois.

---

## 10. Points à confronter avec les experts externes

1. Le faux succès est-il pour eux **un problème de prompt** (convergence), **de gate** (déterministe), ou **de contrat de plan** (réponse finale statique) ?
2. Une porte déterministe « gaps + zéro perception -> refus » est-elle acceptable, ou risque-t-elle de bloquer des plans partiels légitimes (réponse honnête « je n'ai pas pu ») ?
3. Faut-il **générer la réponse finale après** la vérification (présentateur seul), au lieu de la planifier ?
4. La forme objet vs `string` doit-elle être corrigée **côté hôte** (schéma `oneOf`) ou **côté runtime** (sérialisation `kind:id`) ?
5. Le schéma `Plan` doit-il être **deux modèles** (LLM-facing / interne) ou un seul avec `exclude` ?
6. La Progressive Disclosure (+1 062 tokens/appel) aide-t-elle réellement un modèle moyen, ou ajoute-t-elle du bruit ?

---

## 11. Annexe — preuves brutes

- `prompts_log/0009_convergence.md.en.md` : gaps présents, aucune preuve du carré, verdict `pass`.
- `user_logs.txt:54-55` : `plan_partial_accepted` + `plan_validation_decision is_valid=true`.
- `user_logs.txt:278-279` : plan avec `output_target_name: null` + rejet des 4 refs.
- `user_logs.txt:288` : troncature `EOF while parsing a string at line 22 column 9114`.
- `user_logs.txt:294` : plan corrigé (alias présents) accepté.
- `user_logs.txt:329/370/432` : `mouse` avec `target: {id, kind}`.
- `user_logs.txt:333/373/435` : `TARGET_REQUIRED`.
- `rpahosttools.cpp:857-867` vs `274-320` vs `2447-2448` : incohérence du gate objet.
- `rpahostprovider.h:253/261` : `target` déclaré `string` et requis.
- `core/solver.py:1553-1560` + `1588-1615` : gaps lus puis LLM appelé sans gate.
- `core/plan_validator.py:1242-1289` : `output_target_name` absent du résumé juge.
- `prompts_log/0011_presentator_output.md.en.md.vars.txt` : pas de gaps.

---

## 12. Confrontation avec le rapport d'expert (27/10) et recap des fixes

Source confrontée : `AgentBridge/expert_proposal.txt` (90 lignes), relu froidement,
chaque affirmation repassée au code et au log.

### 12.1 Verdict sur les 4 points de l'expert

| # | Affirmation de l'expert | Verdict | Preuve / correction |
|---|---|---|---|
| 1 | « La membrane TargetRegistry fonctionne après le patch » | **CONFIRMÉ** | `bind_target_alias` + publication post-ingress ; plan 02:59:19 accepté et `target` exécuté |
| 2 | « Le blocage `mouse` n'est PAS un bug Python, c'est un bug de garde C++ » | **PARTIELLEMENT VRAI — responsabilité partagée** | Le gate C++ est bien le rejet mécanique (`rpahosttools.cpp:857-859`, `.toString()` sur un `QJsonObject`). Mais ManAgent émet une valeur qui **contredit le manifeste qu'il a reçu** (`target` déclaré `type: string`, `rpahostprovider.h:253/261`) et `ToolsManager.validate_tool_call` **ne vérifie aucun type** (`tools_manager.py:686-717`). Le correctif proposé est en plus **incomplet** |
| 3 | « Faux succès de convergence + empoisonnement du Learner » | **CONFIRMÉ (preuve dure)** | Log 02:32:06 : `Profile inséré : ee848a24 / open paint` puis `skill_lifecycle … consecutive_count=1 … skill.open.paint` ; code : `solver.py:978` -> `_handle_skill_lifecycle_post_execution(is_success=True)` -> `record_execution_result_vectorial(is_success=…)` (`1718-1721`) et `registry.record_run_metric(success=True)` (`1860`) |
| 4 | « Faille dans `core/convergence.py` » | **CHEMIN FAUX** | Ce fichier n'existe pas ; le juge final est `core/solver.py::_verify_mission_convergence` (`1479-1636`) |

### 12.2 Points de l'expert rejetés ou corrigés

1. **« PD > 4 500 chars -> EOF Pydantic » : FAUX.** L'appel tronqué (02:55:17 -> échec
   02:56:39) avait une section PD de **4 223 caractères**, sous le seuil allégué ; des PD de
   4 589 et 4 633 caractères n'ont produit aucune troncature. Surtout, la PD est une
   **entrée** : elle ne peut pas tronquer une **sortie**. Cause réelle = sortie coupée :
   `LLM_STRUCTURED_MAX_OUTPUT_TOKENS = 8192` (`constants.py:212`) passé au provider
   (`planner.py:300` -> `gemini_provider.py:231-232`), possiblement consommé par les jetons
   de raisonnement. À trancher en journalisant `finish_reason` + `thoughts_token_count`
   (absents du log actuel : 0 occurrence de `usage`/`thought`).
2. **« Refuser `plan_partial_accepted` si le plan ne fait que préparer » : REJETÉ.** Le
   contrôle proposé repose sur une heuristique de verbes (multilingue, fragile) et
   remplacerait un faux succès par une boucle de rejets de plans. L'acceptation partielle est
   déjà bornée (`plan_validator.py:1525-1546` : risque faible/moyen, aucune action
   irréversible, pas de confirmation humaine). Le trou n'est pas là : il est dans
   **l'absence de porte de sortie honnête** (convergence + rapport).
3. **« Auto-assigner `output_target_name` depuis le `label` » : REJETÉ.** Le consommateur
   invente un autre nom (`$@_target_rectangle`) que le `label` (`outil_rectangle`) : l'alias
   auto ne résout donc pas la référence, et il masque l'erreur du modèle. Rendre le champ
   obligatoire casserait toutes les étapes sans consommateur. Remplacer par : exemple concret
   dans le prompt + message d'erreur nommant le champ `output_target_name`.
4. **« > 15 erreurs 503 consécutives entre 02:33 et 02:48, gel de 350 s » : CHIFFRES FAUX.**
   Il y a **6** 503 entre 02:33:54 et 02:34:40 (46 s), **20** sur toute la session. Le gel
   réel est un **silence de 296 s** (02:34:45 -> 02:39:41), cohérent avec un appel HTTP
   **sans timeout** (`_execute_with_failover`, `gemini_provider.py:91-160`), pas avec la somme
   des cooldowns. La recommandation (timeout + coupe-circuit) reste valable, mais le point
   d'application n'est pas celui indiqué.
5. **« Patcher l'hôte d'abord, puis le cerveau » : ORDRE INVERSÉ.** Le correctif egress
   (sérialisation conforme au type déclaré) débloque sans recompiler l'hôte. Le patch hôte
   devient de la défense en profondeur, à transmettre à l'équipe CondiFlow.
6. **Correctif hôte incomplet.** `mouse` (`:857`) **et** `keyboard` (`:1253`) portent le même
   gate. Pour `keyboard` le défaut est **silencieux** : `hasTargetArg=false` -> aucun pré-clic,
   aucune erreur (`:1335-1341`), la frappe part au focus courant. `target` (`:2490`) et
   `vision` (`:2968`) acceptent déjà l'objet : l'incohérence est bien interne, sur 2 sites.

### 12.3 Recap des fixes à effectuer (ordre d'exécution)

**P0 — débloquer et supprimer le faux succès (4 correctifs, ~2 h avec tests)**

| # | Fix | Emplacement | Critère d'acceptation |
|---|---|---|---|
| F1 | **Egress cible conforme au type déclaré** : si le schéma du paramètre consommateur déclare `type: "string"` et que `ref.value` est un objet `{kind,id}`, émettre `"<kind>:<id>"` ; sinon laisser tel quel | `core/target_contract.py` (`ConsumerSpec` + `_parse_consumer:321-335` + `resolve_consumer_arguments:163`) | plan de mission 2 rejoué -> `mouse` reçoit une **chaîne** ; `HostSim` durci (objet -> `TARGET_REQUIRED`) reste vert |
| F2 | **Porte déterministe de convergence** : si `plan_gaps` non vide **et** aucune preuve de perception non vide depuis l'acceptation du plan -> `False` **avant** l'appel LLM, avec raison explicite ; si aucun outil de perception n'existe, porte désactivée + warning | `core/solver.py::_verify_mission_convergence` (avant `1588`) ; trail déjà remis à zéro par plan (`executor.py:110`) | mission 1 rejouée -> refus sans appel LLM ; gaps + perception non vide -> LLM consulté ; mission sans gaps -> LLM consulté |
| F3 | **Rapport honnête** : injecter `plan_gaps`, `is_partial`, `convergence_reason` dans le présentateur et interdire « mission accomplie » si gaps ; corriger `presentator_result.status` codé en dur `"success"` | `core/orchestrator.py:1881-1889` et `1896-1901`, `core/presentator.py`, `prompts/{base,en,fr}/presentator_output.md` | les 3 variables apparaissent dans le prompt rendu ; un run avec gaps ne peut pas produire « Mission accomplie » |
| F4 | **Prompt convergence** : « tu juges la MISSION, pas la dernière étape » (`step_description` = contexte), règle 4 explicitement inapplicable à une lacune, règle 9 prioritaire | `prompts/{base,en,fr}/convergence.md` | rendu du prompt vérifié par test ; règle 4 porte la mention « jamais pour une lacune » |

**P1 — réduire les erreurs de validation à la source (~1 h)**

| # | Fix | Emplacement |
|---|---|---|
| F5 | 3e exemple planner : `target {cell,label}` + `output_target_name` -> `mouse {"target":"$@_<alias>"}` + item de checklist | `prompts/{base,en,fr}/planner.md` |
| F6 | Message d'erreur ciblé : si la référence non résolue est un paramètre consommateur de cible, dire « déclare `output_target_name` sur le producteur » (au lieu de suggérer `output_variable_name`) | `core/plan_validator.py:528-530`, `core/planner.py:417-422` |
| F7 | `plan_summary` : afficher `target=<alias>` pour que le juge voie la chaîne cible | `core/plan_validator.py:1286-1288` |
| F8 | Sortie de plan plus maigre : retirer du schéma LLM les 2 champs purement internes (`status`, `result_context`) ; garder `tool_name`/`tool_args_json`/`response_text` (ils sont l'entrée du LLM) | `core/plan_models.py:225-230` |

**P2 — robustesse / coût / observabilité (~1 h)**

| # | Fix | Emplacement |
|---|---|---|
| F9 | Refus hôte déterministe après dispatch (`TARGET_REQUIRED`, `TARGET_SCHEMA_INVALID`) : **1** nouvel essai max, pas de cycle faisabilité+planner complet | `core/plan_models.py:596-604`, `core/solver.py:917-933` |
| F10 | `plan_validation_decision` : ajouter `is_partial` + `partial_gaps` (paquet et event) | `core/orchestrator.py:2270-2292` |
| F11 | Timeout par appel + coupe-circuit sur le failover Gemini (le gel de 296 s n'est pas un problème de cooldown) | `providers/gemini_provider.py:91-160` |
| F12 | Journaliser `finish_reason` + jetons de raisonnement pour trancher la troncature JSON | `providers/gemini_provider.py:241-246` |

**Hors périmètre (à transmettre à l'équipe hôte, non appliqué ici)**
`rpahosttools.cpp:857` et `:1253` : accepter `QJsonObject` dans `hasTargetArg`
(`isObject() || !toString().trimmed().isEmpty()`) ; aligner le manifeste `target`
(`oneOf[string, object]` ou `type: string` assumé) ; rendre explicite l'échec de `keyboard`
quand une cible objet est fournie.

### 12.4 Décisions verrouillées par cette confrontation

- **Pas de patch hôte** de notre côté : F1 suffit à débloquer, et respecte la contrainte
  « ne pas modifier l'hôte ».
- **Aucun assouplissement** de `find_unresolved_plan_refs` : les références invalides restent
  rejetées par la validation déterministe, avant tout juge LLM.
- **Aucun auto-alias** : le contrat reste explicite ; on corrige l'exemple et le message.
- **Pas de refus des plans partiels** : on garde l'acceptation bornée, on ajoute la sortie
  honnête (F2 + F3).

---

## 13. Implémentation P0 (F1-F4) — 28/10/2026

Lot validé par l'utilisateur : **P0 complet**, tests rouges d'abord. Suite complète : **643 passed**
(618 avant le lot, +25 tests). Aucun commit, aucun push, aucun patch hôte.

| # | Fix | Fichiers | Tests |
|---|---|---|---|
| F1 | Egress conforme au type déclaré : `{kind,id}` -> `"<kind>:<id>"` quand le schéma dit `string` | `core/target_contract.py` (`ConsumerSpec.wire_type`, `_wire_value`, `_parse_consumer`, `resolve_consumer_arguments`) | `tests/test_targets_egress_wire_format.py` (6), `tests/test_targets_egress_object_e2e.py` (2) |
| F2 | Porte déterministe : lacunes non vides + zéro preuve de perception non vide -> refus **avant** le LLM ; porte désactivée sans outil de perception | `core/solver.py` (`gap_convergence_refusal`, `_verify_mission_convergence`) | `tests/test_convergence_gap_gate.py` (9) |
| F3 | Rapport honnête : `plan_gaps` / `is_partial` / `convergence_reason` au présentateur, fallback « partiel », `presentator_result.status` réel, `convergence.check.detail` sans « Mission accomplie » sur plan partiel | `core/presentator.py` (`collect_plan_gaps`, `_build_fallback_output`), `core/orchestrator.py`, `prompts/{base,en,fr}/presentator_output.md` | `tests/test_presentator_gaps.py` (5) |
| F4 | Prompt de convergence : périmètre = MISSION, règle 4 inapplicable à une lacune, règle 9 prioritaire | `prompts/{base,en,fr}/convergence.md` | `tests/test_convergence_prompt_mission.py` (3) |

**Banc d'essai durci** : `tests/host_sim.py::host_target_gate` reproduit le gate C++
(`rpahosttools.cpp:857-859`) — un `target` objet est refusé `TARGET_REQUIRED`, comme en production.

**Non-régression assumée** : `tests/test_targets_egress.py` déclare désormais le paramètre
consommateur en `type: "object"` (hôte qui attend la référence brute) ; le cas `string` est
couvert par le nouveau fichier wire-format. Les deux comportements restent testés.

**Détail F2** : le verdict de convergence est mémorisé dans
`runtime_state.last_convergence_ok` / `last_convergence_reason`, consommé par F3.

**Reste à faire (non inclus)** : F5-F12 (§12.3), dont le timeout Gemini (F11), et le ticket hôte
(`hasTargetArg` objet sur `mouse` **et** `keyboard`).

---

## 14. Implémentation P1/P2 (F5-F12) — 28/10/2026

P1 = réduire les erreurs de validation à la source. P2 = robustesse / coût. Même méthode :
test rouge d'abord, puis implémentation, puis suite complète. Aucun commit, aucun push,
aucun patch hôte.

Suite complète : **669 passed** (643 après P0, +26 tests).

| # | Fix | Fichiers | Tests |
|---|---|---|---|
| F5 | Exemple complet de chaîne cible (producteur `output_target_name` -> consommateur `$@_<alias>`) + item de checklist, gated `targets_enabled` | `prompts/{base,en,fr}/planner.md` | `tests/test_planner_target_example.py` (3) |
| F6 | Message de rejet orienté : une référence non résolue sur un paramètre **consommateur de cible** renvoie vers `output_target_name` (et non `output_variable_name`) | `core/plan_validator.py` (`find_unresolved_plan_refs(plan, target_consumers)`), mapping construit depuis `ToolsManager.target_contract_names()` | `tests/test_plan_ref_target_hint.py` (3) |
| F7 | Résumé de plan pour le juge : `target=<alias>` visible sur le step producteur, pour que la chaîne ne ressemble pas à une référence inventée | `core/plan_validator.py` (`_summarize_plan_for_prompt`) | `tests/test_plan_summary_target.py` (2) |
| F8 | Schéma exposé au LLM allégé : `status` et `result_context` (écrits par l'exécuteur) sortis du JSON Schema via `SkipJsonSchema` ; défauts et `model_dump()` inchangés | `core/plan_models.py` | `tests/test_plan_schema_lean.py` (3) |
| F9 | Coupe-circuit sur refus **hôte** de contrat cible (`TARGET_REQUIRED`, `TARGET_SCHEMA_INVALID`) : un seul essai de correction, abandon explicite au 2e refus | `core/plan_models.py` (`is_host_target_contract_refusal`, `host_refusal_budget_exhausted`, `HOST_REFUSAL_MAX_CORRECTIONS=1`), `core/solver.py` (boucle d'exécution) | `tests/test_host_refusal_circuit.py` (4), `tests/test_host_refusal_solver_loop.py` (3) |
| F10 | Event `plan_validation_decision` : `is_partial` + `partial_gaps` (plafonné à 8) exposés au front et à l'observabilité, via un payload unique | `core/plan_models.py` (`build_plan_validation_payload`), `core/orchestrator.py` | `tests/test_plan_validation_event_partial.py` (3) |
| F11 | Anti-gel Gemini : timeout par appel (`LLM_CALL_TIMEOUT_SECONDS=90`) + budget global de failover (`LLM_FAILOVER_BUDGET_SECONDS=180`) -> `ProviderServiceUnavailableError` au lieu d'un silence de 296 s | `providers/gemini_provider.py` (`_execute_with_failover`), `core/constants.py` | `tests/test_gemini_failover_timeout.py` (5) |
| F12 | Diagnostic de fin d'appel : `finish_reason`, `thoughts_token_count`, `candidates_token_count`, warning explicite si `MAX_TOKENS` (troncature de sortie) | `providers/gemini_provider.py` (`finish_diagnostics`, `generate_structured_output`) | idem F11 |

**Distinctions verrouillées par F9** (ne pas confondre les trois cas) :

- `TARGET_NOT_FOUND` : cible périmée côté hôte -> **recréer** peut réparer, aucun coupe-circuit ;
- refus pré-dispatch « unresolved target reference » : déjà couvert en P0 (`is_nonretryable_contract_failure`), abandon immédiat ;
- refus **post-dispatch** de contrat (`TARGET_REQUIRED` / `TARGET_SCHEMA_INVALID`) : une correction, puis abandon (F9).

**Détail F11** : le timeout unitaire est plafonné par le budget restant (`min(timeout, remaining)`),
donc le pire cas total reste borné par la deadline, rotations et backoffs compris. Un timeout
n'entraîne **pas** de rotation aveugle sur les autres clés (un appel muet n'est pas un quota épuisé).

**Reste à faire (non inclus)** : ticket hôte (`hasTargetArg` objet sur `mouse` **et** `keyboard`,
`rpahosttools.cpp:857-859` / `1253-1255`) et validation en mission réelle Paint avec l'hôte.


---

## 15. Run Paint du 28/10/2026 (21:40-21:47) - verdict et correctifs

Source : `CondiFlow/user_logs.txt` (487 lignes) + `observability/events.jsonl`
(payloads `llm_call`, `plan_validation_decision`). Mission `b39a11ae-608a-463e-b965-b5090f3f1300`.

### 15.1 Chronologie utile

| Heure | Événement | Lecture |
|---|---|---|
| 21:41:47 | plan 1 rejeté sans juge : args illisibles (`...}???should_world_state...`) | rejet déterministe correct |
| 21:42:01 | plan 2 (5 étapes) accepté **partiel** (`plan_partial_accepted`, 3 gaps) | F10 : `is_partial=true` visible |
| 21:42:33 | convergence tour 1 : « aucun carré tracé » | juge honnête, pas de faux succès |
| 21:42:56 | plan 3 rejeté : `$@_canvas_start` inconnu (`core/planner.py:418`) | rejet correct, message perfectible (voir 15.3) |
| 21:43:13 | plan 4 accepté ; `target G2` -> clic (650,150) -> **flèche** | G2 ne contient pas le rectangle |
| 21:44:26 | convergence tour 2 : « forme en V inversé » | le juge voit la flèche |
| 21:45:19 | plan 5 : `target F2` (outil rectangle) + canvas `D4` -> `F6` | F2 = **losange**, pas rectangle |
| 21:45:52 | vision : « Rectangle en **G2** ; canvas **B3-H5** » | réponse vision disponible |
| 21:46:06-15 | press (350,350), move (550,550), release (550,550) | `F6` = y 550 = bord bas du canvas |
| 21:46:31 | vision : « lignes formant un angle / chevron » | losange coupé en bas |
| 21:46:34 | convergence tour 3 : échec malgré booléens `true` | `_verify_mission_convergence` (P0) fonctionne |
| 21:46:50 | rallonge refusée (stagnation, 3 tentatives) | juge de rallonge honnête |
| 21:47:12 | rapport final d'échec + leçon `final_report_generation_failure` | **fausse imputation** (voir 15.3) |

### 15.2 Responsabilités

**Hôte : aucune sur ce run.** `target` renvoie `img:...`, `mouse` l'accepte, les
coordonnées loguées sont exactes (`F2` -> 550,150 ; `D4` -> 350,350 ; `F6` -> 550,550).
Le ticket `hasTargetArg` est **corrigé dans le working tree hôte** (non commité) :
le gate utilise `.toString()` et le manifeste déclare `target: {"type":"string"}`
(`rpahostprovider.h:253`). Le run 21:40 prouve la chaîne de bout en bout.
**Message au pont** : écrit le 28/10 (voir §16.5) — la feature `target` n'est pas
dans HEAD et les questions UI/zoom/grille restent ouvertes.

**Modèle : responsable principal.**
1. *Vision* : affirme « outil Rectangle en G2 » ; le clic G2 produit une **flèche**,
   et F2 produit un **losange**. Le repérage de l'icône est faux (cellules voisines).
2. *Planner* : ignore la cellule G2 donnée par la vision et choisit **F2** ; ne vérifie
   jamais que l'outil rectangle est actif avant de dessiner ; place l'arrivée sur `F6`,
   soit le **bord bas** du canvas déclaré `B3-H5`, d'où la forme coupée.
3. *Protocole de plan* : le planner produit un plan **statique** ; il ne peut donc pas
   lire la réponse de l'étape de perception pour choisir la cellule, et **devine** F2.
   La vision est interrogée puis ignorée, faute de planification en deux temps.
4. *Juge de plan* : accepte un plan dont la sélection d'outil n'est pas prouvée.

**Brain : pas de faux succès, mais deux bugs et trois angles morts.**
- Aucun faux `pass` : rejets déterministes corrects, plan partiel signalé, convergence
  et rallonge honnêtes, F11/F12 opérationnels (rotations 503 sans gel).
- Bug 1 (corrigé) : `Analyzer._analyze_presentator_failure` traitait
  `presentator_result.status == "failed"` (statut de la **mission**) comme un échec de
  **génération du rapport**. Chaque mission échouée produisait une leçon contre le
  Presentator. Le vrai échec de génération est stocké sans `user_response`.
- Bug 2 (corrigé) : la validation legacy `core/planner.py` court-circuitait F6 ; le
  message ne parlait que de `output_variable_name` pour une référence de **cible**.
- Angle mort 1 (corrigé par prompt) : aucune obligation de vérifier le contrôle
  sélectionné avant une action dépendante.
- Angle mort 2 (corrigé par prompt) : aucune règle de marge pour les gestes de glisser ;
  un point sur le bord de la région perçue peut être coupé.
- Angle mort 3 (corrigé par prompt) : le planner devinait la cellule au lieu de s'arrêter
  après la perception en plan partiel, puis de replanifier avec la réponse.
- Observabilité : `prompts_log/` est alimenté **quand** `MANAGENT_RECORD_PROMPTS=1`
  est positionné. Le run 21:40 l'avait (fichiers `0047`–`0073`) ; sans la variable,
  les prompts restent dans `events.jsonl`. Le lot de 22:20 (335 fichiers) est un run
  de tests, pas une mission.
- **Constat non corrigé** : dans l'arbre d'exécution, les trois échecs de convergence
  ont `target_entity=None`. `_traverse_tree` n'analyse que le dernier échec ; sans
  entité, `_generate_avoid_lesson` sort immédiatement. Résultat : aucune leçon Planner
  sur le vrai problème (targeting), alors que le faux chemin Presentator, lui, a parlé.
  À décider : attribuer les échecs de convergence au Planner (doctrine blame-shifting)
  ou les laisser non attribués pour éviter du bruit.

### 15.3 Correctifs agnostiques (tests rouges d'abord)

| # | Fix | Fichiers | Tests |
|---|---|---|---|
| L1 | Leçon Presentator : n'analyser que si le **rapport** n'a pas pu être généré (`generation_failed` ou absence de `user_response`/`system_summary`) | `core/learner.py` (`is_presentator_generation_failure`), `core/orchestrator.py` (fallback explicite) | `tests/test_learner_presentator_blame.py` (5) |
| L2 | Validation legacy : si la référence inconnue alimente un argument `target`, indiquer `output_target_name` (au lieu de laisser F6 court-circuité) | `core/planner.py` (`_used_as_target_arg`) | `tests/test_planner_unknown_target_hint.py` (2) |
| L3 | Planner : (a) un clic réussi ne prouve pas que le contrôle visé est actif -> étape de perception après sélection ; (b) petit contrôle -> lecture fine (zoom) avant `target` ; (c) glisser avec au moins une case de marge dans la région perçue ; (d) si une étape a besoin de la réponse d'une perception, arrêter le plan en partiel honnête puis replanifier (aucune valeur devinée) | `prompts/{base,en,fr}/planner.md` | `tests/test_planner_verification_rules.py` (2) |
| L4 | Données : désactivation de la leçon fausse `final_report_generation_failure` (id 484, Presentator) | `memory.db` (`is_active=0`, réversible) | vérifié en base |

Suite complète : **678 passed** (669 avant, +9).

### 15.4 Suite probable

1. Rejouer la mission Paint avec l'hôte pour vérifier que le planner ajoute bien la
   vérification post-sélection et reste dans `B3-H5` avec marge.
2. Si la vision continue à mal repérer les icônes du ruban : privilégier `vision zoom`
   sur la cellule avant `target` (règle déjà dans le prompt) et, côté hôte, envisager
   une grille plus fine ou un ancrage UIA du ruban (chantier hôte, non demandé ici).
3. Surveiller les leçons `Presentator` : plus aucune ne doit apparaître sur une mission
   simplement échouée.
4. Décider du sort des échecs de convergence non attribués (`target_entity=None`) :
   les imputer au Planner pour que le Learner produise enfin une leçon sur le targeting,
   ou assumer qu'ils ne génèrent rien.

---

## 16. Tokens, Progressive Disclosure et `prompts_log` — diagnostic du 28/10

### 16.1 Où partent les tokens (mission `b39a11ae`)

| Tag | Appels | Prompt tokens | Completion | PD ~tokens | PD % |
|---|---:|---:|---:|---:|---:|
| Plan | 5 | 106 146 | 5 045 | 4 743 | 4,5 % |
| PlanValidationDecision | 3 | 39 330 | 342 | 3 578 | 9,1 % |
| FeasibilityDecision | 2 | 26 258 | 414 | 2 262 | 8,6 % |
| Presentator_output | 1 | 20 394 | 297 | 536 | 2,6 % |
| ConvergenceDecision | 3 | 17 722 | 322 | 2 404 | 13,6 % |
| perceive_understand | 4 | 13 114 | 317 | 0 | 0 % |
| RetryExtensionDecision | 1 | 4 848 | 88 | 1 207 | 24,9 % |
| MissionCompactor | 1 | 1 670 | 170 | 0 | 0 % |
| ExtractedLesson | 1 | 934 | 116 | 0 | 0 % |
| **Total** | **21** | **230 416** | **7 111** | **14 731** | **6,4 %** |

Lecture : `Plan` = 46 % du prompt total à lui seul (5 tentatives). Les 5 plans
coûtent 106 146 tokens ; les autres tags de jugement/rapport ~124 000.

### 16.2 Progressive Disclosure : 6,4 %, pas le coupable

- 89 `llm_call` dans `events.jsonl` : 58 portent la section PD ; **1 seule** requête
  de découverte réelle (`OrchestratorDecision`, mission `7ccc2321`, `missions/last_mission`).
- Mission `b39a11ae` : **0 requête** de découverte pour **14 731 tokens** de PD.
- Section PD : 2 144–5 231 caractères selon le schéma (~536–1 307 tokens).
- Parts par tag : Plan 4,1 %, PlanValidation 9,8 %, Orchestrator 9,9 %, Feasibility 9,2 %,
  Convergence 13,8 %, Presentator 4,5 %, RetryExtension 24,9 %.
- Conclusion : la PD n'est pas le poste principal (6,8 % sur l'ensemble des events,
  6,4 % sur la mission). Elle est surtout **inutilisée** (1 requête / 58 appels).
  Piste future, non implémentée : section PD complète seulement au premier appel
  d'une entité, rappel court ensuite, ou flag `PD_MODE=full|compact|off`.

### 16.3 Le vrai gras : le bloc `REGISTRE (MÉTADONNÉES)` du contexte

| Prompt | Taille | Bloc registre | Part |
|---|---:|---:|---:|
| Plan #3 | 36 413 | 4 549 | 12,5 % |
| Plan #4 | 39 254 | 6 762 | 17,2 % |
| Plan #5 | 46 319 | 8 723 | 18,8 % |
| Presentator | 26 910 | 14 472 | 53,8 % |
| Feasibility | 15 959 | 3 997 | 25,0 % |

Le contexte `Already tried + errors` passe de 36 à 12 467 caractères en 5 plans ;
la croissance est **entièrement** le bloc registre (Plan #4 -> #5 : +3 801 caractères,
dont +1 961 de registre + ~1 840 de trace). Or les prompts ont déjà une section
registre compacte (`## REGISTRY` / `registry_compact`).

**Fix L5** : `_format_registry_view` ne rend plus qu'un résumé borné (nombre,
répartition par type, 12 noms, renvoi vers la section registre). Tests
`tests/test_context_registry_bloat.py` (5), rouges avant, verts après.
Gain mesuré sur le run : ~8,4 Ko sur le contexte du 5e plan, ~14,2 Ko sur le
Presentator, ~3,7 Ko sur la Feasibility ; ~9 200 tokens au total sur les prompts
portant le bloc (~4 % de la mission).
Suite complète : **683 passed** (678 avant, +5).

### 16.4 `prompts_log` : 1 111 prompts rendus, 60 % de doublons

- 1 111 `.md` (5,31 Mo) + 1 111 `.vars.txt` (414 Ko) = 2 222 fichiers, 5,72 Mo.
- 28 templates ; `planner.md` = 162 fichiers / 1,95 Mo (max 42 Ko), `convergence.md`
  116 / 476 Ko, `orchestrator.md` 43 / 453 Ko, `presentator_output.md` 56 / 430 Ko,
  `plan_validation.md` 49 / 400 Ko, `feasibility.md` 85 / 329 Ko.
- **119 groupes de doublons, 974 fichiers, 3,18 Mo (60 %)** : les retries réécrivent
  le même prompt rendu (ex. `0085_planner.md.base.md` x4).
- Le run 21:40 **a bien enregistré** (fichiers `0047`–`0073`) : `MANAGENT_RECORD_PROMPTS=1`
  était positionné. Le lot de 22:20 (335 fichiers) est un run de tests (tous templates
  et langues), pas une mission.
- `.vars.txt` ne contient que les **noms** de variables (586 o), pas leurs valeurs ;
  le `.md` contient le prompt rendu. `events.jsonl` reste la source pour les tokens.
- Le plus gros `planner` (42 Ko) est le 5e plan du run : il porte les 12,5 Ko de contexte
  et les 8,7 Ko de registre. Le fix L5 réduit les prochains prompts.
- Recommandation (non implémentée) : rétention (N derniers runs) ou dédoublonnage par
  hash à l'écriture, sinon le dossier croît indéfiniment.

### 16.5 Pont hôte

`brain_to_host.md` écrit le 28/10 : verdict hôte (0 faute), questions sur les cibles
UI/image (`kind_reason`, `uia_candidates`, `cell_rect`, `point`), la sémantique de
`vision zoom`, la grille contrastée, le faux positif de re-détection UIA (repli sans
`Name`), l'incohérence `hasTargetArg` / `s_parseTargetRef`, et le commit de la feature
`target` (absente de HEAD `c3fbdf7`). Aucun patch hôte.

### 16.6 Leviers restants

- Statique répétée à chaque plan : `TOOLS` ~10 Ko + `STEP GRAMMAR` ~2,9 Ko, soit
  ~3 200 tokens par plan ; 5 plans = ~16 000 tokens. Un cache de prompt côté
  provider ou un rappel plus court en retry serait le prochain levier.
- Réduire le nombre de plans (validation plus stricte en amont) reste plus rentable
  que toute micro-optimisation de prompt.

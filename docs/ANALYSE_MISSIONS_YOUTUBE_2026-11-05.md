# Analyse à froid — 3 missions YouTube (05/11/2026)

Missions : `gemini-3.5-flash` (M1), `gemini-3.5-flash-lite` (M2), `gemini-2.5-flash` (M3).
Sources : `observability/events.jsonl` (non tronqué), `CondiFlow/user_logs.txt` (logs hôte),
dumps de travail dans `%TEMP%\opencode\mission_events\`.

Méthode : lecture des logs et des prompts rendus **avant** de confronter les ressentis
(consignés séparément dans `AgentBridge/brain_notes.md`, entrée 05/11 « Zone mémoire »).

---

## 1. Réponse directe : ce que le LLM voyait au moment du `perceive` raté

Trois LLM différents interviennent, et il faut les distinguer :

| Acteur | Ce qu'il voyait | Ce qu'il ne voyait pas |
|---|---|---|
| **Planner / Solver** | objectif, manifeste d'outils, snapshot monde (`get_world_state`, 290–851 chars), historique d'échec textuel | la liste des candidates, l'image, le fait qu'un élément manquait |
| **Sélecteur de cible** (`_choose_target_with_llm`) | `Cible recherchee : …` + N lignes `- clé: description (scope=…)` | **l'image**, le `role`, le `rect`, le flag `interactive`, le `focused` |
| **Juge de convergence** | réponse finale + snapshot monde | les causes réelles des échecs intermédiaires |

Preuve M1 (prompt de sélection, 19:11:16) — 24 candidates, toutes des éléments
de chrome navigateur, aucune barre de recherche de la page YouTube :

```
- target_ui_preset_34432273: Barre d'adresse et de recherche (scope=ui_preset)
- target_ui_preset_483172ae: Installer YouTube (scope=ui_preset)
- target_ui_preset_4b19627d: YouTube (scope=ui_preset)
...
Réponse : {'target_key': '', 'reason': 'Aucune candidate dans la liste ne correspond
à la barre de recherche interne de la page YouTube.'}
```

Le sélecteur a donc **correctement refusé de deviner**. Il n'a jamais reçu la capture :
`_choose_target_with_llm` ne construit que des lignes de texte
(`tools/internal_tools.py:873-887`).

Et la trace d'observabilité a détruit la preuve : `_emit_alignment_failure` fait
`str(observed)[:500]` sur `out["data"]` (`tools/internal_tools.py:824-832`), or pour
`vision` ce dict contient `image_base64` → les 500 premiers caractères sont du base64,
pas la liste des candidates. C'est pour cela que le `alignment_failure` de M1 ne permet
pas de reconstituer ce que le modèle avait sous les yeux.

Conclusion R2 : au moment du `perceive` raté, le cerveau voyait **des descriptions seules**.
Ni capture, ni géométrie, ni rôle. Et la trace ne conservait pas non plus les candidates.

---

## 2. Mission 1 — `gemini-3.5-flash`

### 2.1 Plan 1 (`plan-00001`, accepté)

11 étapes : `win+r` → texte URL → `enter` → `perceive_understand` « barre de recherche
YouTube » → clic → texte → `enter` → `perceive_understand` « chaîne officielle » → clic →
`perceive_understand` extraction → `direct_answer`.

Le plan est **structurellement correct** : `source_args={"mode":"image"}` est bien présent,
`target_producer=target`, `target_description` renseigné.

Échec step_4 : `TARGET_ANCHOR_UNSUPPORTED`, « Aucune candidate ne correspond à
« barre de recherche YouTube » ». Cause réelle : **l'hôte n'a pas extrait le champ de
recherche web dans `data.presets`**. Les candidates sont les éléments UIA du chrome
navigateur (Réduire, Restaurer, Fermer, Retour, Avancer, Actualiser, favoris, omnibox).
Le cerveau n'a pas fauté ; il n'a pas deviné, ce qui est le comportement demandé.

### 2.2 Plan 2 (`plan-00013`, accepté)

Le modèle change de stratégie : `ctrl+l` → texte URL → `enter` → `perceive_understand`
« chaîne officielle ».

Logs hôte :

```
[Orchestrator] Requête d'outil interceptée -> keyboard
  {"hotkey":"ctrl+l","mode":"hotkey","target":"none"}
```

Les trois appels retournent `Success: true`. Mais le titre de fenêtre passe de
`YouTube - Google Chrome` à :

```
AutoCUsehttps://www.youtube.com - YouTube - Google Chrome
```

Donc `ctrl+l` **n'a pas focalisé la barre d'adresse**, la saisie s'est **concaténée** au
contenu du champ de recherche YouTube qui avait encore le focus (« AutoCUse » + URL), et
la page a lancé une recherche sur cette chaîne. L'hôte a répondu « succès » sans effet
réel, et le cerveau n'a pas vérifié l'effet avant de taper.

### 2.3 Arrêt

Après 2 échecs d'exécution :

```
Abandon : même échec répété sans aucun progrès matériel. Inutile de relancer à l'identique.
```

Preuve calculée : `normalize_failure_signature` tronque à **200 caractères**
(`core/plan_models.py:681-694`). Les deux raisons (« barre de recherche YouTube » vs
« la chaîne officielle AutoCUse ») ne divergent qu'après le 200e caractère → signatures
identiques → `is_sterile_streak` renvoie `True`. Deux échecs **différents** ont été vus
comme le même.

Aggravant : les 5 retours `SolverResult(FAILED)` de `executor.py` (lignes 126, 271, 316,
338, 422) **n'incluent pas `material_success_count`**. Le compteur vaut donc toujours 0
sur un plan échoué, alors que des étapes ont réussi (les 3 premiers `keyboard` du plan 2).
Le garde-fou « progrès » est aveugle dans les deux sens.

### 2.4 Message final

« l'outil de reconnaissance visuelle n'a pas réussi à identifier et cibler la chaîne
officielle AutoCUse » : vrai pour le dernier `perceive`, mais **faux comme cause
principale**. La mission a échoué sur (a) une extraction de candidates incomplète côté
hôte, puis (b) un `ctrl+l target="none"` silencieusement sans effet. Le message final
n'oriente pas vers la vraie réparation.

---

## 3. Mission 2 — `gemini-3.5-flash-lite`

**Aucun plan n'a été exécuté.** 3 rejets, 0 action matérielle.

| Tentative | Plan | Rejet | Cause |
|---|---|---|---|
| 1 | `plan-00021` | validation | `step_1: producteur 'candidates' appele directement` |
| 2 | `plan-00022` | superviseur | `perceive_understand expected_result='any'` non réutilisé + `direct_answer` sans action |
| 3 | `plan-00023` | validation | même motif qu'en 1 |

Plan 1 : `step_1 vision {"mode":"image"}` avec `output_variable_name=data_initial_screen`,
puis `step_2 perceive_understand` **correct** (avec `source_args`), puis `direct_answer`.
Le modèle voulait **une capture brute** pour la lire ensuite. Le validateur refuse tout
appel direct d'un outil qui déclare `produces … mode=candidates`
(`core/plan_validator.py:517-522`).

Le message « producteur 'candidates' appele directement » est **sémantiquement faux** pour
l'intention du modèle : il ne consomme pas de candidate, il prend une capture. Et il
n'indique pas la réécriture correcte. Le modèle sur-corrige : plan 2 = un `perceive` qui
ne sert à rien + une réponse d'abandon (« Impossible d'accéder à YouTube… »), rejeté par le
superviseur. Plan 3 = retour au même `vision` direct.

Arrêt à `MAX_PREEXECUTION_FAILURES=3` : la mission est morte **sans avoir jamais touché le
monde**. Le compteur `attempt_number` affiche 3/3 alors qu'il n'y a eu **0 tentative
d'exécution**.

Note : le résumé final dit « rejets successifs de la part du Superviseur » alors qu'il y a
eu **2 rejets du validateur + 1 du superviseur**. Ce n'est pas la même cause ni la même
réparation.

---

## 4. Mission 3 — `gemini-2.5-flash`

### 4.1 Tentative 1 (`plan-00024`, rejet validation)

`step_7 vision {"mode":"image"}` direct pour capturer la page chaîne → même refus que M2.
Aucune exécution.

### 4.2 Tentative 2 (`plan-00025`, accepté, 1er échec d'exécution)

Plan correct : `windows+r` → `chrome.exe` → `enter` → `perceive_action` (barre d'adresse,
`action_args` texte `youtube.com`) → `perceive_action` (barre de recherche) → `perceive_action`
(clic chaîne) → `perceive_understand` extraction.

Échec step_4 :

```
La source 'vision' est mal appelée : Paramètre requis 'mode' manquant
pour l'outil 'vision' (fournis: []).
```

Tous les `perceive_action` de ce plan **omettent `source_args`**. C'est exactement ce que
montre l'EXEMPLE 3 du prompt rendu : un `perceive_action` complet **sans `source_args`**.
Le modèle a copié l'exemple. Ce n'est pas une erreur du modèle, c'est un **exemple
invalide** qui lui a été servi.

### 4.3 Tentative 3 (`plan-00034`, accepté, 2e échec d'exécution)

Le modèle corrige : `source_args={"mode":"image"}`. L'ancrage fonctionne —
preuve, le sélecteur choisit bien la barre d'adresse parmi les candidates :

```
Cible recherchee : la barre d'adresse de Chrome
...
- target_ui_preset_34432273: Barre d'adresse et de recherche (scope=ui_preset)
Réponse : {'target_key': 'target_ui_preset_34432273', ...}
```

Mais l'action envoie `"text":"youtube.com[ENTER]"`. L'hôte refuse proprement :

```
Touche collée au texte : 'youtube.com[ENTER]'. Utilisez press_key("ENTER")
ou séparez : 'texte → [ENTER]'. Rien n'a été tapé.
```

C'est un **refus sain** de l'hôte. Aucune touche n'a été tapée.

### 4.4 Tentative 4 (`plan-00040`, accepté, 3e échec d'exécution)

Le modèle revient à des étapes brutes :

```
step_1 keyboard {"mode":"text","text":"youtube.com"}          # sans target
step_2 keyboard {"mode":"key","key":"[ENTER]"}                # sans target
```

step_1 réussit (l'hôte autorise `mode=text` sans cible). step_2 est refusé :

```
Ancrage obligatoire : key sans cible est refuse (risque de frappe dans le vide).
Passez target=<cible UI produite par target>, ou target="none" pour une commande
systeme globale.
```

Le message dit lui-même que `target="none"` aurait suffi. En M1, la même étape
(`key enter target="none"`) avait été acceptée. Ici le modèle a **omis** `target`.
Budget épuisé (3 exécutions) → arrêt, alors que la mission était **à un argument**
de continuer.

### 4.5 Preuve que le flux composite fonctionne

La tentative 3 a ancré la barre d'adresse via `perceive_action` et le sélecteur a choisi
la bonne candidate. Le flux `vision → candidates → choix → injection du nom local dans
`target preset=tN` → action` est donc **opérationnel** quand l'élément est présent.
Le problème n'est pas l'architecture du ciblage ; c'est l'alimentation des exemples,
la classification des échecs et le budget.

---

## 5. Les cinq bugs de boucle qui expliquent la majorité des arrêts

Ces bugs sont indépendants des modèles et se cumulent.

### B1 — `material_success_count` perdu sur tout plan échoué

`core/executor.py` : seuls les retours SUCCESS renseignent `material_success_count` et
`tool_call_count`. Les 5 retours FAILED les laissent à 0.

Conséquences :
- `_maybe_extend_budget` exige `last_mat > 0` (`core/solver.py:1722-1726`) → **aucune
  rallonge n'est jamais demandée** sur un plan échoué. En M3, aucun event
  `retry_extension_decision` n'existe, alors que step_1 avait réussi.
- `is_sterile_streak` compare `last_mat <= best_before` avec des compteurs toujours 0
  → seul le texte de signature décide. D'où le faux « même échec » de M1.

### B2 — Signature d'échec tronquée qui fusionne des causes différentes

`normalize_failure_signature` place la classe puis la raison, et coupe à 200 caractères.
Le préfixe commun (« Divergence détectée à l'étape … Rejet matériel … L'outil a renvoyé
le statut 'false' … ») mange tout le budget avant la partie discriminante.
Il faut mettre **les champs discriminants en tête** (`classe|outil|code`) et ne tronquer
que la prose finale.

### B3 — Budget pré-exécution confondu avec budget d'exécution

M2 : 3 rejets statiques = mission terminée, 0 action. M3 : la tentative 1 (rejet) porte
le numéro 1, puis « Tentative 3/3 » s'affiche à la 4e tentative réelle. Le modèle et
l'utilisateur ne peuvent pas savoir combien d'actions il reste.

### B4 — Refus hôte non typé, donc non classifiable

L'hôte renvoie « Ancrage obligatoire… » en texte libre, sans `error.code`. Or
`is_host_target_contract_refusal` ne reconnaît que `TARGET_REQUIRED` /
`TARGET_SCHEMA_INVALID` (`core/plan_models.py:753-767`). Donc :
- le budget « 1 correction puis abandon » ne s'applique pas ;
- l'échec est classé `convergence_failure` comme un échec du monde ;
- la santé outil et la stérilité ne distinguent pas « outil mal configuré » de
  « outil exécuté et en panne ».

À l'inverse, M1 `ctrl+l target="none"` renvoie `Success: true` **sans effet** : l'hôte
n'a pas de moyen de dire « accepté mais sans effet observable ».

### B5 — Exemple 3 du prompt sans `source_args`

L'EXEMPLE 3 est dérivé du manifeste réel (`target_example_context`) mais n'expose que
`target_consumer_args`. Il ne dérive pas les **arguments requis de la source**.
Conséquence directe : M3 plan 25 copie l'exemple et omet `mode`, échec immédiat.
Le validateur ne pouvait pas l'attraper : il ne vérifie pas les requis de `source_args`.

### B6 (bonus) — Trace d'alignement inutilisable

`str(observed)[:500]` sur un dict contenant `image_base64` : la trace ne contient pas
les candidates. Impossible de répondre à « qu'est-ce que le modèle voyait ? » après coup.

---

## 6. Confrontation ressentis ↔ preuves

### R1 — « `target="none"` : ni surprise ni refus bizarre »

**Confirmé, et le problème est double dans les logs :**

1. `target="none"` est **accepté là où il ne peut pas marcher** : `ctrl+l` en M1 renvoie
   `Success: true` mais ne focalise pas la barre d'adresse. Le cerveau croit avoir agi.
2. `target` **omis** est refusé pour `key`, alors que le remède proposé est précisément
   `target="none"`. Le modèle n'a pas eu le droit de retenter (budget).

Donc le cerveau ne « commande » pas encore : il subit une sémantique de `none` non
documentée et non observable. Fix : `none` = opt-out explicite unique, refus **typé**
quand la cible est absente, et **rapport d'effet** (`focus_window`, `target_used`,
`changed`) pour que le cerveau vérifie au lieu de supposer.

### R2 — « Savoir ce que le LLM voyait »

**Répondu** : descriptions seules, pas d'image, pas de géométrie. Et la trace ne le
montrait pas (base64 tronqué). Fix : trace structurée des candidates + enrichissement du
sélecteur (`role`/`rect`/`interactive` quand déclarés) + diagnostic « N candidates vues,
aucune ne contient X ».

### R3 — « Ne compter la redondance que si l'outil est bien configuré et échoue réellement »

**Confirmé par le code** : ce n'est pas seulement non implémenté, c'est **inversé**.
Aujourd'hui :
- M1 : deux échecs **différents** comptés comme identiques → abandon injustifié ;
- M3 : des succès matériels réels comptés 0 → aucune rallonge, arrêt injustifié.

Fix : taxonomie d'origine (`ARGS_INVALID`, `HOST_REFUSAL`, `TOOL_ERROR`, `ALIGNMENT`,
`PLAN_REJECTED`, `CONVERGENCE`) ; seuls `TOOL_ERROR` alimentent la santé outil et la
stérilité ; remise à zéro au prochain succès ; compteur cumulatif monotone.

### R4 — « Échouer pour la vraie raison, ne pas s'arrêter s'il reste une correction évidente »

**Confirmé** :
- M1 blâme la vision alors que la cause est l'extraction hôte puis le clavier.
- M2 blâme le Superviseur alors que 2 rejets sur 3 viennent du validateur.
- M3 s'arrête à un `target="none"` près.
- Le message « Tentative 3/3 » masque qu'une des tentatives n'a jamais exécuté.

---

## 7. Fixes proposés

### P0 — Boucle de contrôle (sans ça, tout le reste est aveugle)

| # | Fix | Fichier | Test |
|---|---|---|---|
| F1 | Propager `material_success_count` + `tool_call_count` dans **tous** les retours FAILED | `core/executor.py:126,271,316,338,422` | plan 1 étape OK + 1 étape KO → `material_success_count == 1` |
| F2 | Signature structurée `classe|outil|code|prose[:120]` | `core/plan_models.py:681`, `core/solver.py:1660` | les 2 raisons M1 donnent 2 signatures ≠ |
| F3 | Stérilité sur compteur **cumulatif** monotone, pas par plan | `core/solver.py:1660`, `plan_models.py:719` | succès puis même échec → non stérile |
| F4 | Taxonomie d'origine des échecs + comptage conditionnel | `plan_models.py`, `solver.py:706-721`, `executor.py` | matrice : args invalides ≠ outil en panne ≠ refus hôte ≠ alignement |
| F5 | Budget pré-exécution séparé (rejets ≠ exécutions) | `core/solver.py:646-760` | 3 rejets + 1 plan valide → le plan s'exécute |

### P1 — Bloquants mission (ce qui a fait échouer M2/M3)

| # | Fix | Fichier | Test |
|---|---|---|---|
| F6 | EXEMPLE 3 dérive `source_args` du schéma source (requis + `enum`/`default`) | `core/planner.py:27`, `prompts/*/planner.md` | manifeste `vision.required=["mode"]` → exemple contient `{"mode":"image"}` |
| F7 | Valider `source_args` (requis) **avant** exécution | `core/plan_validator.py` | plan sans `source_args` rejeté avant dispatch |
| F8 | Message de rejet actionnable pour appel direct d'un producteur de candidates | `core/plan_validator.py:517-522` | message contient le nom de l'outil + la réécriture exacte |
| F9 | Refus hôte typé `TARGET_REQUIRED` (y compris quand le texte suggère `none`) | hôte + `plan_models.py:753` | HostSim renvoie un code → classé `HOST_REFUSAL`, pas `TOOL_ERROR` |
| F10 | `none` : opt-out unique et explicite ; omission = refus typé avec remède | hôte + prompt | `key` sans target → refus typé une fois, puis correction acceptée |

### P2 — Diagnostic (pour ne plus jamais être aveugle)

| # | Fix | Fichier |
|---|---|---|
| F11 | `alignment_failure` : candidates structurées, jamais de base64, `candidates_total`, `image_present` | `tools/internal_tools.py:806-840` |
| F12 | `TargetEntry` + prompt sélecteur : `role`, `rect`, `interactive`, `focused` quand déclarés | `core/targets.py:136`, `tools/internal_tools.py:861` |
| F13 | Message final : distinguer rejets validation / superviseur / exécution / alignement, citer la dernière cause concrète | `core/solver.py`, convergence |
| F14 | Si aucune candidate : `TARGET_ANCHOR_UNSUPPORTED` avec `candidates_seen` + 5 descriptions | `tools/internal_tools.py` |

### P3 — Stratégie prompt

| # | Fix | Fichier |
|---|---|---|
| F15 | Règle « élément absent → ne pas répéter la même description ; ancrer la barre d'adresse via `perceive_action` puis aller par URL » | `prompts/*/planner.md` |
| F16 | Règle « après une frappe/navigation, vérifier titre/URL avant de taper ailleurs » | `prompts/*/planner.md` |

### Extension de contrat proposée à l'hôte (agnostique)

Pour éviter de coder `keyboard`/`hotkey` en dur côté cerveau, ajouter au schéma un
`required_when` déclaratif :

```json
"required_when": [
  {"param": "mode", "in": ["key", "hotkey"], "require": ["target"]}
]
```

Le cerveau pourrait alors refuser **avant dispatch** (`TOOL_ARGS_INVALID`) sans connaître
le vocabulaire de l'hôte. À défaut, le refus typé `TARGET_REQUIRED` suffit, mais il coûte
une tentative d'exécution.

---

## 8. Ce que je propose de faire maintenant

Ordre recommandé : **F1 → F2/F3 → F4/F5** (boucle), puis **F6/F7/F8** (déblocage M2/M3),
puis **F9/F10** (contrat hôte), puis **F11/F12/F13** (diagnostic), **F15/F16** (prompt).

Les P0/P1 sont entièrement côté ManAgent et testables sur HostSim, sans toucher CondiFlow.
F9/F10 nécessitent une évolution hôte (communication via `AgentBridge`, pas de modification
directe de CondiFlow).

Aucun commit, aucun push avant GO explicite.

---

## 9. Annexes — preuves brutes

- `alignment_failure` M1 : `events.jsonl`, 19:11:16, `step_4-00005`.
- Prompt sélecteur M1 (24 candidates, aucune barre de recherche) : `llm_call` tag
  `perceive_understand`, 19:11:16.
- Titre avant/après retry M1 : `user_logs.txt:558` et `user_logs.txt:1033`.
- Refus `key` sans cible M3 : `attempt_failed`, 19:24:46, `step_2-00042`.
- Touche collée M3 : `attempt_failed`, 19:24:05, `step_1-00035`.
- `mode` manquant M3 : `attempt_failed`, 19:23:16, `step_4-00029`.
- Rejets M2 : `plan_rejected_validation` 19:18:45 et 19:19:21,
  `plan_rejected_supervisor` 19:19:04.
- Calcul de la fausse stérilité M1 : signatures de longueur 200, `EQUAL: True`.


---

## 10. Addendum 06/11 ? L'image : ce qu'elle est devenue, et pourquoi le ciblage l'ignore

### 10.1 Trajet r?el de l'image

1. `vision mode=image` renvoie `data.image_base64` + `data.presets` + `data.grid`
   (`{tile_px, cols, rows, origin}`) + `delivered_w/h`.
2. `perceive_understand` lit la d?claration `returns` de l'h?te
   (`field=image_base64, asset=image/jpeg`) et d?code l'image en `BinaryDataAsset`
   (`core/discovery/data_asset.py:extract_typed_payloads`).
3. Le base64 est remplac? par `[Image : <uri>]` dans une copie `cleaned`, et
   `media_assets` garde les octets. L'image est donc **bel et bien disponible**.
4. **Mais** :
   - `perceive_action` passe `_anchor_only=True`
     (`tools/internal_tools.py:1414`) ? l'analyse LLM est saut?e ;
   - `perceive_understand` avec `target_producer`/`output_target_name` ex?cute la
     s?lection **avant** toute analyse ; si elle ?choue, il `return _sel_err`
     (`tools/internal_tools.py:1280`) ? l'image n'est jamais montr?e ;
   - `_choose_target_with_llm` ne re?oit que `selection` + `question`, pas
     `media_assets` (`tools/internal_tools.py:861-887`).
5. R?sultat : l'image est d?cod?e, enregistr?e, puis **abandonn?e au moment
   pr?cis o? elle devenait utile**. Elle n'entre ni dans le registre de
   variables, ni dans la trace d'alignement (qui stocke le payload *avant*
   nettoyage, donc du base64).

### 10.2 Combien d'images, et o?

| Mission | Images captur?es | Devenir |
|---|---|---|
| M1 | 2 (`step_4` recherche, `step_8` cha?ne) | d?cod?es, puis jet?es sur `TARGET_ANCHOR_UNSUPPORTED` |
| M2 | 0 | aucun plan ex?cut? |
| M3 | 1 (`plan-00034 step_1`, 19:24:04) | d?cod?e, utilis?e pour ancrer l'omnibox, puis jet?e ; l'action ?choue sur `youtube.com[ENTER]` |

### 10.3 Ce que le s?lecteur peut faire ? r?ponse directe

Non, il ne peut pas ? lancer l'inspection pour creuser ?.

- `_TargetSelectionResult` h?rite de `BaseModel`, pas de `BaseDiscoverySchema`
  (`tools/internal_tools.py:1344`). Donc `generate_structured` le traite en mode
  legacy : **aucune boucle de progressive disclosure**.
- M?me avec PD, la PD de ManAgent route vers des *DataProviders* (fichiers,
  registre, historique), pas vers des outils d'?cran.
- Le s?lecteur n'a aucun acc?s outil : il re?oit un prompt texte et renvoie une
  cl?. Il ne peut ni demander `inspect`, ni d?signer une case, ni voir l'image.

La PD existe dans le socle, mais elle n'est **pas branch?e** sur le ciblage UI.

### 10.4 Diagnostic honn?te

L'impl?mentation actuelle traite la liste de candidates comme **la seule v?rit?
d'ancrage**. L'image et la grille sont des effets de bord de la perception, pas
des sources de ciblage. Quand la liste par d?faut (24 presets) ne contient pas
l'?l?ment :

- le cerveau ne ? bypasse ? pas volontairement la d?tection : il n'a **aucun
  chemin de code** pour demander une inspection ou utiliser les pixels ;
- il retourne `TARGET_ANCHOR_UNSUPPORTED` et replanifie ;
- l'image est perdue.

C'est exactement l'?cart avec l'intention ? image + candidates comme contextes
compl?mentaires, puis creuser si besoin ?.

### 10.5 Design cible : ? anchor or disclose ? (born?, g?n?rique)

```
obs      = source(source_args)                     # image + presets + grid
cands    = project(obs, contract)
choix    = select_text(cands, question)
si choix: ancrer(choix); fin

# 1. ?largir la moisson (param?tres d?clar?s seulement)
obs2     = source(max_presets=MAX, focus_hint=question)      # puis mode=annotated
cands2   = project(obs2, contract)
choix    = select_text(cands2, question)
si choix: ancrer(choix); fin

# 2. ancrage visuel (l'image est enfin utilis?e)
region   = ground_image(obs.image, obs.grid, question)       # -> point/box normalis?
si region:
    obs3 = source(inspect_args + region_param=region, with_image=true)
    cands3 = project(obs3, contract)
    choix = select_text(cands3, question)                    # ou select avec image
    si choix: ancrer(choix); fin

# 3. refus honn?te, avec ce qui a ?t? vu
return TARGET_ANCHOR_UNSUPPORTED(candidates_seen, image_used=true,
                                 probes=["image","annotated","inspect"])
```

Garde-fous : 2 appels h?te suppl?mentaires maximum, 1 appel LLM image maximum,
et jamais d'action sans cible d?clar?e. Chaque sonde est trac?e.

### 10.6 Ce qui est n?cessaire c?t? contrat (agnostique)

Ajouter au `target_contract` de l'outil de perception un bloc `disclosure` :

```json
"disclosure": {
  "inspect": {"mode": "inspect", "with_image": true, "max_elements": 60},
  "region":  {"param": "cell", "scheme": "A1", "grid": "data.grid"},
  "produces": ["data.elements"]
}
```

Le cerveau n'a alors aucun nom d'outil en dur : il lit la capacit? d?clar?e.
? d?faut, deux replis g?n?riques :

- `fields` des candidates ?tendu ? `role`, `rect`, `cells`, `interactive` ;
- si le sch?ma de la source expose un `enum` contenant `inspect` et un param?tre
  de r?gion (`cell`/`preset`), l'utiliser comme disclosure implicite.

Pour ?viter le calcul de `A1` c?t? cerveau, le plus propre est que l'h?te accepte
une r?gion normalis?e (`region: {x,y,w,h}`) en plus de `cell`.

### 10.7 Port?e des fixes

| Fix | C?t? | D?pend de l'h?te |
|---|---|---|
| Sonde `max_presets` + `focus_hint` + `annotated` avant refus | ManAgent | non (param?tres d?clar?s) |
| `ground_image` (image + grille ? r?gion) | ManAgent | non (le provider sait d?j? attacher les images) |
| `inspect` d?clench? par `disclosure` | ManAgent + contrat | oui (bloc d?claratif) |
| Cellule `A1` calcul?e depuis `grid` | ManAgent | non, mais convention ? documenter |
| Trace `alignment_failure` avec image_used / probes / candidates | ManAgent | non |
| `role`/`rect`/`cells` dans le prompt s?lecteur | ManAgent + contrat `fields` | oui (3 champs) |

C'est le cha?non manquant entre ? percevoir ? et ? agir ? : sans lui, les fixes
P0/P1 rendent la boucle honn?te, mais ne rendent pas la cible trouvable.

---

## 11. Addendum 06/11 ? ce qui est impl?ment? (Lots 1-4)

Cette section remplace le sch?ma **propos?** en 10.6 : le bloc final est
`target_contract.disclosure` version 1, avec `views[]`, `anchors[]` et
`probes[]` (voir `docs/HOST_CONTRACT.md` ?3i). Aucun nom d'outil h?te ni type
de cible h?te n'est cod? dans le cerveau.

### 11.1 Ciblage visible (Lot 1)

- `core/targeting.py` : `ObservedWorld` = payload brut + m?dias d?cod?s +
  candidates projet?es (+ attributs) + vues nomm?es + historique des sondes.
- `TargetDecision` (h?rite de `BaseDiscoverySchema`) : `target_key`,
  `target_kind` (`local`|`anchor`), `anchor_kind`, `no_target`, `reason`,
  `discovery_request`. `local` = cl? du registre ; `anchor` = valeur brute
  d'un type d?clar? par l'h?te.
- `resolve_target` : boucle born?e ? anchor or disclose ?, **2 sondes max**.
  Le s?lecteur est appel? avec `with_discovery=False` : c'est la boucle de
  ciblage qui r?sout la demande, jamais le `DiscoveryEngine` g?n?rique.
- M?dias : les charges `returns` sont d?cod?es, remplac?es par un marqueur
  `[Image : uri]` dans le texte et pass?es au s?lecteur par `media_assets`.
  Aucun base64 dans le prompt.

### 11.2 P0/P1 (Lot 2)

- Les 5 retours `SolverResult(FAILED)` de `core/executor.py` portent
  `material_success_count`, `tool_call_count`, `skipped_crucial_ids` et
  `failed_tool`.
- `normalize_failure_signature` produit
  `classe|outil|origine_ou_code|prose[:120]|digest` : les causes diff?rentes
  ne fusionnent plus.
- `classify_failure_origin` : `ARGS_INVALID`, `HOST_REFUSAL`, `TOOL_ERROR`,
  `ALIGNMENT`, `PLAN_REJECTED`, `CONVERGENCE`, `USER_CANCELLED`, `UNKNOWN`.
- Sant? outil et st?rilit? g?n?rique ne comptent que `TOOL_ERROR`/`UNKNOWN`.
  La convergence a son propre disjoncteur : un recul d?j? effectu? + m?me
  divergence ? l'identique = arr?t honn?te, pas une boucle ? 3 plans.

### 11.3 Inspection d?clar?e et ancres (Lot 3)

- `fields.attributes` expose au s?lecteur des champs libres d?clar?s
  (`role`, `rect`, `cells`, `angle`...), jamais la r?f?rence.
- `disclosure.views[]` : vues nomm?es (`name` libre, `path` point?).
- `disclosure.anchors[]` : `kind` est un vocabulaire H?TE (cell, file, key,
  joint, symbol...), `param` le consommateur qui re?oit la valeur brute.
- `disclosure.probes[]` : `id`, `kind` **libre**, `tool`, `args` fixes,
  `hint_param`, `subject` optionnel, `produces`.
- Sonde `subject.kind = "local"` : nom local si le param?tre est un
  consommateur d?clar? (egress r?sout), sinon r?f?rence brute. Tout autre
  `kind` est une ancre d?clar?e et exige `raw: allow`.
- Cible finale ancre : `kind` d?clar? + consommateur `raw: allow`
  correspondant, sinon `TARGET_ANCHOR_UNSUPPORTED` sans appeler le producteur.
- Refus honn?tes : `TARGET_ANCHOR_UNSUPPORTED`, `TARGET_PROBE_LOOP` (m?me
  sonde + m?me sujet). Aucune cible devin?e, aucun outil invent?.

### 11.4 Tests et documentation (Lot 4)

- `tests/test_target_disclosure_flow.py` : contrat g?n?rique, attributs,
  sonde puis retente, sonde locale (nom local vs r?f?rence brute), vues
  nomm?es + m?dias, ancre inconnue refus?e, ancre finale, refus sans
  consommateur brut.
- `tests/test_target_hosts_agnostic.py` : calculatrice d?terministe **sans
  aucun outil de perception**, robot (articulations + attributs), IDE (ancre
  `file` puis cible locale).
- Suites cibl?es vertes, puis suite compl?te : **808 passed**.
- `docs/HOST_CONTRACT.md` FR/EN, `docs/host.manifest.schema.json` et
  `docs/host.manifest.example.json` synchronis?s.
- Pont h?te : `AgentBridge/brain_to_host.md` d?crit le bloc g?n?rique ?
  ajouter c?t? CondiFlow. Aucun commit/push avant GO explicite.

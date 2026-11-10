# Analyse à froid — mission YouTube `265353f1-b295-4813-bc6d-b4ce5af4a418`

**Date de la mission** : 06/11/2026, 12:31:39 → 12:37:45 (6 min 06 s)
**Hôte** : CondiFlow / Chrome, chaîne `AutoCUse`
**Résultat** : succès (3 abonnés, 2 vidéos)
**Objet** : comprendre pourquoi une mission simple a coûté 4 tentatives et 19 appels LLM, et proposer des correctifs **agnostiques du harnais et des prompts** (sans modifier CondiFlow).

> Toutes les preuves viennent de `observability/events.jsonl`, `prompts_log/*` et `C:\Users\CHRISTIAN\Documents\QtProjets\CondiFlow\user_logs.txt`.
> Aucun fichier de CondiFlow n'a été modifié.

---

## 1. Chiffres clés

| Indicateur | Valeur |
|---|---|
| Tentatives | **4** (alors que `max_attempts = 3`) |
| Étape de rupture | T1 `step_5-00069`, T2 validation, T3 `step_4-00080` |
| Appels LLM jusqu'à convergence | **19** |
| dont faisabilité | 2 (initiale + recul après T1) |
| dont génération de plan | 4 (T1, T2, T3, T4) |
| dont validation de plan | 3 (T1, T3, T4 ; T2 rejetée par le superviseur) |
| dont perception (`perceive_understand`) | **9** pour 5 étapes de perception exécutées |
| dont convergence | 1 |
| Avec presentator + leçons | 22 |
| Tokens (snapshot avant presentator) | **199 306** prompt / 4 986 completion |
| Appels d'outils hôte exécutés | 5 (T1) + 0 (T2) + 4 (T3) + 4 (T4) |
| Avertissements `perception_unconsumed_warning` | 2, **tous les deux faux positifs** |

Le coût n'est pas dominé par les actions hôte (13 au total) mais par la **re-planification** : 4 plans + 3 validations + 2 faisabilités = 9 appels de « réflexion », auxquels s'ajoutent 9 appels de perception pour seulement 5 étapes perçues.

---

## 2. Chronologie

| Heure | Événement | Commentaire |
|---|---|---|
| 12:31:39 | `session_turn`, `retriever_query` | |
| 12:31:57 | `FeasibilityDecision` + `attempt_started` T1 (`plan-00064`) | Stratégie initiale correcte |
| 12:32:21 | `Plan` T1 | 10 étapes, `is_valid: true`, risque `low` |
| 12:32:32 | Validation T1 acceptée | 2 warnings de perception non lus (faux positifs) |
| 12:32:32 → 12:33:04 | 4 étapes réussies, échec `step_5-00069` | Voir §3.1 |
| 12:33:18 | `FeasibilityDecision` (recul) | Contient déjà « valider avec la touche Entrée » |
| 12:33:47 | `Plan` T2 (`plan-00075`) | Rejetée par le superviseur, 0 action |
| 12:34:15 | `Plan` T3 (`plan-00076`) | Repart de zéro, 7 étapes |
| 12:34:33 | Validation T3 acceptée | |
| 12:34:59 → 12:35:46 | 3 étapes « réussies », échec `step_4-00080` | Voir §3.3 |
| 12:36:04 | `Plan` T4 (`plan-00084`) | 5 étapes, reprend au `Enter` |
| 12:36:37 → 12:37:23 | Exécution + convergence `pass` | Succès |
| 12:37:44 | `Presentator_output` | 3 abonnés / 2 vidéos |

---

## 3. Anatomie des quatre tentatives

### 3.1 T1 — le premier échec est un **mismatch de type de cible**, pas un échec de perception

`plan-00064` était structurellement bon :

- `step_4-00068` : `perceive_understand` → **produit** `$@_barre_recherche` avec `target_args.kind = "auto"`.
- L'hôte résout cette cible en **image** : `kind: "img"`, `resolution: "cell_image"`, `label: "barre_recherche"`, cellule E2, `kind_reason: "uia_no_cover"`.
- `step_5-00069` : `keyboard mode=text text="AutoCUse" target="$@_barre_recherche"`.

Refus hôte immédiat :

> `Cible image : le clavier exige une cible UI.`

Source hôte : `CondiFlow/Agentic/rpahosttools.cpp:1459-1462` — le clavier valide le type de cible même en `mode=text` dès qu'une cible est fournie.

**Cause racine** : le plan a demandé une cible `auto` pour un **champ de saisie**, sans forcer `kind=ui`, alors que le besoin du consommateur (`keyboard`) était connu dès la génération. Le harnais n'a aucune vérification de compatibilité producteur/consommateur avant exécution : il a fallu que l'hôte refuse, après 4 étapes déjà exécutées.

**Preuve que le plan savait quoi faire ensuite** : `step_6` de ce même plan était déjà `keyboard mode=key key=enter`. La connaissance du `Enter` n'a donc jamais été perdue à ce stade — elle sera perdue en T3.

### 3.2 T2 — un nom de cible visible dans le registre mais interdit par le validateur

`plan-00075` réutilise `$@_barre_recherche` produit par **T1**, sans le redéclarer. Rejet superviseur :

> `référence devinée step_1: target=$@_barre_recherche (inconnu : déclarez ce nom via output_target_name sur l'outil producteur de cible (step antérieur))`

Or le prompt `0091_planner` liste explicitement ce nom dans sa section `## REGISTRY (names already produced)` :

```
- $@_barre_recherche (target) : Cible 'barre_recherche' (target)
```

**Cause racine** : contradiction entre deux contrats.

- `core/plan_validator.py:303` (`_plan_declared_ref_names`) n'accepte que les noms déclarés par un step **antérieur du plan courant**.
- `core/solver.py` / `core/prompt_loader.py` exposent au planner les noms des **tentatives précédentes** comme s'ils étaient utilisables.

Le registre « invite » donc à une référence que le validateur refuse. C'est un piège déterministe, indépendant du modèle.

Coût : 1 génération de plan complète (28 s) + 1 rejet + 1 tentative consommée.

### 3.3 T3 — deux fautes distinctes, dont une totalement silencieuse

`plan-00076` :

1. `step_1-00077` : re-perception de la barre de recherche, `target_args.kind = "auto"`, nom déclaré `barre_recherche_ui`.
   → le nom dit « ui », mais la cible produite est **image** (`label: "image E2"`, `target_kind: "img"` au clic `step_2-00078`). Le clic souris l'accepte ; toute consommation clavier aurait été refusée, exactement comme en T1.
2. `step_3-00079` : `keyboard mode=text text="AutoCUse\n"` — **le `Enter` est encodé dans le texte**.
   → l'hôte tape le `\n` comme un caractère : `characters_sent: 9` (8 lettres + 1 saut de ligne). La recherche n'est **jamais soumise**. L'étape est pourtant marquée réussie (`typed: true`), donc aucune alerte.
3. `step_4-00080` : `perceive_understand` avec `target_description = "Le lien vers la chaîne AutoCUse"` et `kind=auto`.
   → le sélecteur de cible choisit `Rechercher` (`target_ui_preset_3a900d84`, ComboBox), au lieu de `no_target=true`.
   → l'analyseur LLM ne reçoit qu'un **crop de la barre de recherche** et répond `success: false` avec une description pourtant riche :

```json
{
  "success": false,
  "data": "Seule la barre de recherche YouTube contenant 'AutoCUse' et son menu déroulant de suggestions... résultats non encore chargés",
  "observed": "Champ de saisie 'Rechercher' visible...",
  "gap": "Les résultats de recherche ne sont pas encore affichés",
  "suggested_targets": ["..."],
  "error_reason": "Le lien vers la chaîne AutoCUse n'est pas présent dans la vue actuelle."
}
```

La description existe et est exploitable. **Elle n'arrive pas au planner** (voir §4.2).

**Causes racines de T3** :

- **Aucune règle déterministe** n'interdit les caractères de contrôle dans `keyboard text`, alors que la doc de l'outil dit « une frappe par appel » et « text = littéral ». La régression est invisible.
- Le **sélecteur de cible décide du champ de vision de l'analyseur**. S'il choisit un crop, l'analyseur ne peut pas élargir : il est structurellement condamné à un `success: false` si l'objet cherché n'est pas dans le crop.
- Le sélecteur a violé sa propre règle 7 (`si aucune cible ne convient : no_target=true`) en substituant l'élément le plus proche.

### 3.4 T4 — reprise correcte, mais par reconstruction

`plan-00084` :

1. `keyboard mode=key key=enter target="none"`
2. `perceive_understand` (résultats) → `chaine_autocuse_target`
3. `mouse click`
4. `perceive_understand` (extraction) → `data_infos_chaine`
5. `direct_answer`

C'est le **plan minimal correct**. Il n'a coûté que 2 perceptions (3 appels LLM avec convergence) et aucune action inutile. Le problème n'est donc pas la capacité du modèle à produire le bon plan : c'est le coût pour y arriver.

---

## 4. Réponses directes aux questions posées

### 4.1 Où le plan a-t-il été faible ?

| Moment | Faiblesse | Nature |
|---|---|---|
| 12:32:21 (T1) | `kind=auto` pour un champ destiné à `keyboard` | **Modèle** (choix d'argument) + **harnais** (aucune validation de compatibilité) |
| 12:32:21 (T1) | `perception_unconsumed_warning` sur step_4 et step_7 | **Harnais** (faux positifs : la sortie est consommée via `output_target_name`) |
| 12:33:47 (T2) | réutilisation d'un nom du registre interdit par le validateur | **Harnais** (contrat contradictoire) |
| 12:34:15 (T3) | perte du `Enter` explicite présent en T1, remplacé par `\n` | **Modèle** (régression) + **harnais** (aucune règle) |
| 12:35:37 (T3) | sélecteur choisit `Rechercher` pour « lien vers la chaîne » | **Modèle** (violation de règle) + **harnais** (pas de self-check) |
| 12:35:37 (T3) | l'analyseur ne voit qu'un crop | **Harnais** (pas d'élargissement automatique) |
| 12:36:04 (T4) | le planner doit déduire `Enter` de signaux indirects | **Harnais** (payload de perception non transmis) |

Le point commun : **les erreurs sont détectées tard, après exécution, et le feedback qui remonte est appauvri**. Le harnais sait beaucoup de choses (types de cibles, arguments, succès partiels) mais ne les utilise pas pour empêcher ou pour expliquer.

### 4.2 Le LLM de perception a-t-il pu décrire le monde lors de l'échec ?

**Oui, et il l'a fait correctement.** Le prompt `llm_analyze_data` contient déjà l'instruction :

> « Si la question demande de TROUVER / LOCALISER… et que rien n'est trouvé ou visible, indique `success: false` et DÉCRIS dans `data` ce que tu vois à la place… cette description sert au replan. »

La réponse de `step_4-00080` contient bien `data`, `observed`, `gap` et `suggested_targets`. `tools/internal_tools.py:324-334` retourne ces champs dans le résultat de l'outil.

**Mais le planner ne les reçoit pas.** Chaîne de perte :

1. `core/executor.py:1927` `_build_failure_trace(...)` ne reçoit que `details` (= `convergence.reason`, une phrase courte).
2. Le rapport d'échec affiché au planner contient donc :
   > `Détail technique : … Le lien vers la chaîne AutoCUse n'est pas présent dans la vue actuelle.`
3. La section `## REGISTRY` ne montre que des métadonnées tronquées : `core/prompt_loader.py:61` `registry_compact()` coupe les descriptions à **120 caractères** et n'affiche que `value_hint`.
4. Vérification factuelle : le prompt `0091_planner.md.en.md` ne contient **ni** « suggestions », **ni** « résultats non encore chargés », **ni** `observed`, **ni** `gap`.

**Conclusion** : le mécanisme de perception d'échec fonctionne ; c'est le **transport du payload vers le replan** qui est cassé. Correctif à fort ROI et purement harnais.

### 4.3 Le premier échec « Cible image : le clavier exige une cible UI… Piste à évaluer… »

Ce message est la concaténation de deux choses de niveaux différents :

- `Cible image : le clavier exige une cible UI.` = **refus hôte** légitime (`rpahosttools.cpp:1462`), correct et précis.
- `Piste à évaluer : faire évoluer l'approche de cette étape, ou ajuster l'attente si le délai est plausible. Vérifie aussi une cause en amont si l'indice est ambigu.` = **texte générique du harnais** (`core/executor.py:452`), identique pour toute non-convergence.

Le générique **dilue** l'information utile. Un message comme « l'étape a produit une cible `img` ; l'outil `keyboard` exige `ui` ; forcez `kind=ui` ou cliquez l'image puis tapez sans cible » aurait été à la fois exact et actionnable. Ici, la « piste » ne mentionne ni le type de cible, ni le champ `kind`.

C'est un défaut de **formulation du feedback**, pas de perception : le harnais connaît le type de la cible (il l'a dans `step`/registre), mais le message de rupture ne le dit pas.

### 4.4 Comment ManAgent a-t-il déduit seul qu'il fallait taper `enter` avant de chercher le lien ?

Par **redondance de signaux**, pas par la perception visuelle. Quatre sources indépendantes étaient disponibles :

1. **La stratégie de recul** (12:33:18, `FeasibilityDecision`) : « … saisir 'AutoCUse' **et valider avec la touche Entrée**. » Elle est répétée dans le prompt T4 (`0091_planner`, ligne 7, « Suggested strategy (advice only) »).
2. **Le plan T1 lui-même** contenait `step_6 : keyboard mode=key key=enter`. Le planner pouvait donc se recopier.
3. **Le rapport d'échec de T3** montre l'étape de rupture `step_4-00080` et, juste au-dessus, `step_3-00079` avec `keyboard mode=text` — aucune étape `key` après la saisie. L'absence de soumission est visible dans la trace.
4. **L'état du monde** au moment du replan : `focus : Rechercher` (le champ est focalisé, le texte est dedans, mais rien n'a été validé) + « le lien n'est pas présent dans la vue » + la description « suggestions affichées, résultats non chargés ».

La source 1 est la plus déterminante : **la bonne action était connue 4 minutes avant la convergence**. Elle a été ignorée par T2 (rejet) puis par T3 (qui a « optimisé » en `\n`). T4 l'a enfin appliquée.

Point important : ce n'est **pas** la perception qui a sauvé la mission, c'est la persistance de la stratégie de recul dans le prompt. Si ce texte avait été absent, la déduction aurait été beaucoup plus incertaine (choisir une suggestion du menu déroulant aurait été une hypothèse concurrente plausible).

---

## 5. Modèle vs harnais — frontière nette

### 5.1 Ce qui relève du modèle (moyen/standard)

- Choisir `kind=auto` pour un champ de saisie alors que `kind=ui` était disponible et que le consommateur était `keyboard`.
- Fusionner « saisir » et « valider » en `text="AutoCUse\n"`, en régression par rapport à son propre plan T1.
- Sélectionner `Rechercher` comme cible du « lien vers la chaîne AutoCUse » (substitution sémantique, violation de la règle 7).
- Réutiliser un nom du registre sans le redéclarer (faute du modèle, mais provoquée par un registre trompeur).

Ces erreurs sont **plausibles pour un modèle standard**. Elles ne sont pas corrigeables uniquement par le prompt : un modèle peut toujours se tromper. Il faut des **gardes déterministes**.

### 5.2 Ce qui relève du harnais/prompts (agnostique, corrigeable)

| # | Défaut | Preuve |
|---|---|---|
| H1 | Le validateur ne vérifie pas la compatibilité `kind` producteur/consommateur | T1 exécute 4 étapes avant le refus hôte |
| H2 | Le payload de perception (`observed`/`gap`/`suggested_targets`) n'atteint pas le replan | `0091_planner` n'en contient aucune trace |
| H3 | Le sélecteur de cible peut réduire le champ de vision sans recours | T3 : crop de la barre de recherche |
| H4 | Les noms des tentatives précédentes sont affichés comme utilisables mais refusés | `barre_recherche` listé, puis « référence devinée » |
| H5 | Les cibles des étapes **échouées** restent publiées avec un nom trompeur | `$@_chaine_autocuse_link` pointe sur `Rechercher` |
| H6 | Le type (`ui`/`img`) n'est pas exposé dans le registre du planner | `(target)` seulement ; un nom `..._ui` peut être `img` |
| H7 | Les caractères de contrôle dans `keyboard text` sont acceptés silencieusement | `characters_sent: 9`, étape marquée réussie |
| H8 | `perception_unconsumed_warning` ignore `output_target_name` | 2 faux positifs sur T1 |
| H9 | Un plan rejeté avant action consomme une tentative | 4 tentatives avec `max_attempts=3` |
| H10 | Le message de non-convergence est générique et perd le contexte matériel | « Piste à évaluer… » |

---

## 6. Correctifs agnostiques, par ordre de ROI

> Tous sont côté ManAgent (`core/`, `tools/`, `prompts/`). Aucun ne dépend d'un hôte particulier : ils s'appuient sur les contrats `kind`, `produces`/`consumes` et sur le payload déjà normalisé.

### P0 — empêcher l'échec ou le rendre auto-explicatif

**P0-1. Validation de compatibilité `kind` avant exécution.**
Étendre `find_target_plan_problems` (`core/plan_validator.py:452`) et le contrat de cible (`tools/internal_tools.py`, `target_contract_names`) :

- `keyboard` en `mode=key|hotkey` : cible **obligatoirement `ui`**.
- `keyboard` en `mode=text` **avec** cible : cible `ui` exigée (sinon proposer `mouse click` sur l'image puis `text` sans cible).
- Producteur `perceive_understand` avec `kind=auto` dont la cible est consommée par `keyboard` : **erreur de plan** actionnable (« forcez `kind=ui` ou consommez avec `mouse` »).

Effet attendu : T1 n'aurait jamais été exécutée ; 0 action gaspillée, 1 tentative économisée.

**P0-2. Transmettre la perception complète au replan.**
`_build_failure_trace` (`core/executor.py:1927`) reçoit une `observation` (la valeur de `data_{step.id}` / `data_*`) et ajoute, borné à ~600 caractères :

```
- Ce que la perception a vu : <data>
- Écart constaté : <gap>
- Candidats suggérés : <suggested_targets>
```

Aux deux sites d'appel (`executor.py:323` et `:441`), passer la valeur du registre pour l'étape en échec. Les champs existent déjà (`tools/internal_tools.py:324-334`).

Effet attendu : le planner lit « la recherche n'est pas soumise / résultats non chargés » au lieu de le reconstituer. Réduit la probabilité d'un T4 exploratoire.

**P0-3. Élargissement automatique borné quand la perception échoue.**
Dans `perceive_understand`, si `success=false` **et** la source était une cible/crop, relancer **une fois** sur la frame complète (`source_args.mode="image"`, sans `target_producer`). Si le second essai réussit, l'étape réussit ; sinon, remonter les deux observations.

Effet attendu : neutralise le mode de défaillance « l'analyseur ne voit que le crop choisi par le sélecteur ». Coût : +1 appel LLM dans le cas d'échec, mais -1 tentative complète.

### P1 — supprimer les pièges déterministes

**P1-4. Interdire les caractères de contrôle dans `keyboard text`.**
Règle de validation : `\n`, `\r`, `\t` dans `text` → erreur « une frappe par appel ; utilisez `mode=key key=enter` ». Ajouter aussi un avertissement explicite dans le détail d'échec si `characters_sent` dépasse la longueur du texte visible.

Effet attendu : la régression T3 devient impossible.

**P1-5. Self-check du sélecteur : pas de substitution sémantique.**
Dans `prompts/base/target_selection.md` (règle 7), imposer :

- la cible choisie doit partager un attribut sémantique avec la question (`description`, `role`, `name`) ;
- si la question porte sur un objet qui n'existe qu'**après** une action non prouvée, répondre `no_target=true` ;
- interdiction explicite de choisir « le champ qui contient la requête » quand la question demande un **résultat**.

Effet attendu : T3 aurait renvoyé `no_target=true`, déclenchant une inspection élargie au lieu d'un crop condamné.

**P1-6. Hygiène du registre entre tentatives.**
Dans `_get_registry_metadata_view` / `registry_compact` :

- annoter les entrées des tentatives précédentes : `(tentative N — non déclarable dans ce plan)` ;
- **ne pas publier** les cibles produites par une étape en échec, ou les marquer `(invalide : étape en échec)` ;
- exposer le `kind` réel sur la ligne cible : `$@_x (target ui)` / `(target img)`.

Effet attendu : supprime le piège T2 et le piège `chaine_autocuse_link → Rechercher`.

**P1-7. Corriger `perception_unconsumed_warning`.**
`find_unconsumed_perception_outputs` (`core/plan_validator.py:787`) doit ajouter `$@_{output_target_name}` aux `needles` et à la détection dans les steps postérieurs.

Effet attendu : -2 faux positifs sur cette mission, -11 sur le journal ; le signal redevient crédible.

**P1-8. Budget de tentatives cohérent.**
Un plan rejeté **avant toute action** (T2) ne devrait pas consommer une tentative, ou devrait être explicitement journalisé comme telle avec un motif. Ici, 4 tentatives ont été exécutées avec `max_attempts=3` : soit le compteur est incohérent, soit la garde est contournée silencieusement.

### P2 — réduire le coût structurel

**P2-9. La stratégie de recul doit être vérifiée, pas seulement conseillée.**
Quand un `refined_strategy` de recul contient une action concrète (« touche Entrée », « cliquer sur X »), ajouter un warning de validation si aucun step ne l'implémente. À défaut, renforcer le prompt : « toute déviation doit être justifiée dans `step_context` ». Le libellé actuel `(advice only)` autorise l'oubli pur et simple — constaté deux fois.

**P2-10. Résolution déterministe des presets.**
Aujourd'hui chaque perception coûte 2 appels LLM (sélection + analyse) : 9 appels pour 8 perceptions. Quand `target_args.kind="ui"` et qu'un preset correspond lexicalement (`focus_hint`/label), résoudre sans appel de sélection. Effet : -2 appels par perception dans le cas nominal.

**P2-11. Recette « champ de recherche » dans le prompt planner.**
Section courte et normative : `mouse click` sur la cible UI → `keyboard text` (sans cible ou avec cible UI) → `keyboard key=enter` → `perceive_understand` sur les résultats. Trois steps, jamais de `\n`. À doubler de P1-4 pour être réellement contraignant.

---

## 7. Trajectoire nominale rejouée

Avec P0-1 + P0-2 + P1-4, la mission aurait donné :

| Étape | Appels LLM cumulés |
|---|---|
| Faisabilité | 1 |
| Plan (cible de recherche en `kind=ui`, texte puis `Enter`) | 1 |
| Validation | 1 |
| Perception barre de recherche (sélection + analyse) | 2 |
| Saisie + `Enter` | 0 |
| Perception résultats | 2 |
| Clic | 0 |
| Perception extraction | 1 |
| Convergence | 1 |
| **Total** | **9** |

Soit **-53 % d'appels** et environ **-55 % de tokens** par rapport aux 19 appels / 199 k tokens observés. Le plancher absolu (avec P2-10) est de **7 appels**.

Le vrai gaspillage identifiable, tentative par tentative :

- T2 : **100 % évitable** (piège de registre).
- T3 : la re-perception de la barre de recherche (2 appels) était évitable ; l'échec de `step_4-00080` (2 appels) aussi.
- T1 : l'échec était évitable par validation statique (0 appel hôte gaspillé, mais 4 étapes exécutées pour rien).

---

## 8. Observations secondaires

- **Le nom d'une cible est une interface, pas une garantie.** `barre_recherche_ui` pointait sur une cible `img` ; `chaine_autocuse_link` pointait sur `Rechercher`. Tant que le `kind` et l'état (valide/échoué) ne sont pas exposés, le planner ne peut pas raisonner correctement.
- **Un succès d'étape n'est pas une preuve d'effet.** `step_3-00079` est `typed: true` alors que la recherche n'est pas soumise. Le harnais devrait distinguer « action injectée » de « effet constaté » (le champ `focus_changed`/`await_change` existe déjà et n'est pas exploité).
- **Les faux positifs détruisent la confiance dans les warnings.** Deux warnings sur T1 étaient faux ; le planner les a ignorés, à raison. Un warning qui crie au loup systématiquement ne protège plus.
- **Le texte « Piste à évaluer… » est identique pour tous les échecs.** Il devrait être remplacé par un diagnostic dérivé du type d'échec (`kind` incompatible, cible absente, focus non prouvé, délai).

---

## 9. Preuves et reproductibilité

- Événements de la mission : `observability/events.jsonl`, filtre `mission_id == 265353f1-…`.
- Plans : `llm_call tag=Plan` (4 occurrences) ; validations : `plan_validation_decision`.
- Perceptions : prompts `0079`, `0087`, `0088`, `0090` (`llm_analyze_data`) et `0087`/`0089` (`target_selection`).
- Rapport d'échec transmis au planner : `prompts_log/0091_planner.md.en.md`, lignes 36-50 et 82-99.
- Refus hôte : `CondiFlow/Agentic/rpahosttools.cpp:1459-1462`.
- Contrat de référence : `core/plan_validator.py:303` (noms déclarables) et `:787` (perception non consommée).
- Formatage du registre : `core/prompt_loader.py:61` (troncature 120 caractères).

Tests de non-régression recommandés pour chaque correctif :

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\compile_i18n.py --check
```

---

## 10. Conclusion

La mission a réussi, mais elle a coûté **deux fois et demie** ce qu'elle aurait dû coûter. Le modèle n'est pas seul en cause : le harnais a laissé passer une cible de type incompatible, n'a pas transmis la description du monde qu'il avait déjà reçue, a affiché des noms de cibles piégeux, et a validé silencieusement un `\n` qui n'a jamais soumis la recherche.

Les trois correctifs à fort effet sont **P0-1** (compatibilité `kind` avant exécution), **P0-2** (payload de perception dans le rapport d'échec) et **P0-3** (élargissement automatique sur échec de perception). Ils sont agnostiques, testables unitairement, et transforment des échecs coûteux en erreurs détectées avant action ou en feedback directement exploitable.

---

## 11. État d'implémentation

| Correctif | État | Choix d'implémentation |
|---|---|---|
| P0-1 | fait, **opt-in** | `consumes[].accepts` + `produces[].kind_param` : le blocage n'existe que si l'hôte déclare `accepts`. Sans déclaration, comportement strictement inchangé. |
| P0-2 | fait | `_build_failure_trace` reçoit `observation` et rend `observed`/`gap`/`suggested_targets` (bornés). |
| P0-3 | fait | Une seule relance d'analyse sur la première observation (frame complète), sans nouvel appel hôte ; les deux observations remontent si l'élargissement échoue. |
| P1-4 | fait | `find_embedded_control_chars`, uniquement si le schéma expose `key`/`hotkey` (signature clavier) : aucun nom d'outil codé en dur. |
| P1-5 | fait | Règle de non-substitution sémantique dans `target_selection.md` (base/en/fr), sans vocabulaire d'outil. |
| P1-6 | fait | `target_kind` + `attempt_no` stockés à la liaison ; vue registre annotée `(target <kind>)` et `(tentative N — non déclarable dans ce plan)` ; alias d'une étape en échec retiré. |
| P1-7 | fait | `$@_{output_target_name}` ajouté aux `needles` des warnings de perception non consommée. |
| P1-8 | fait, **observabilité seule** | `attempt_started`/`attempt_failed` portent `execution_attempt` et `counts_toward_budget` ; la sémantique du budget n'est pas modifiée. |
| P2-9 | fait | `find_strategy_step_gaps` : warning si un recul concret n'est implémenté par aucun step. |
| P2-10 | fait | Résolution déterministe d'une correspondance exacte **unique** avant le sélecteur, uniquement quand le plan demande explicitement un type de cible (`kind_param` du contrat). |
| P2-11 | **retiré du prompt** | Une « recette » nommant des outils computer-use violerait l'agnosticité. La contrainte est portée par le code (P1-4), qui refuse les caractères de contrôle avant exécution. |

Tests ajoutés : `tests/test_p0_p2_fixes.py` (11 cas) ; suite complète et `compile_i18n.py --check` verts.

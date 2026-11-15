# Roadmap résiduelle — après P0/P1 (run `dcc9ec4b`)

Ce document est la mémoire du chantier. Il ne décrit **que ce qui reste**, avec
pour chaque point : la cause mesurée, le correctif attendu, et le critère qui
dira « c'est fait ». Rien ici n'est nécessaire à la suite immédiate : le P0 et
le P1 sont livrés, testés et couverts par des bancs de non-régression.

Référence des correctifs livrés : `docs/CHANTIER_TARGET.md`,
`tests/test_mission_dcc9ec4b_regression.py` et
`tests/fixtures/dcc9ec4b_replay.jsonl` (rejeu doré de la mission).

---

## 1. Watchdog d'appel (C1-bis)

**Cause mesurée.** Un appel peut rester muet jusqu'au plafond de sa classe
(600 s planner/juge, 300 s analyse, 120 s sélecteur). C'est honnête, mais un
fournisseur qui ne répond jamais bloque la mission jusqu'à 10 minutes sans
signe de vie intermédiaire.

**Correctif attendu.** Dans `core/llm.py`, un battement périodique (ex. toutes
les 30 s) qui :
- émet `llm_call.stall` avec `kind`, `elapsed_s`, `budget_s` ;
- ne raccourcit **jamais** le budget (le plafond reste la seule autorité) ;
- laisse l'hôte décider s'il veut annuler (le canal d'annulation existe déjà).

**Fait quand** : un appel bloqué produit au moins un événement par tranche de
30 s, et un test montre qu'aucun budget n'est réduit par le watchdog.

**Pourquoi différé** : le P0 corrige la cause (budgets réalistes). Le watchdog
n'ajoute que du confort d'observabilité, et il touche au transport — donc au
risque le plus élevé pour le plus faible gain immédiat.

---

## 2. Streaming des réponses structurées (C2-bis)

**Cause mesurée.** Le provider attend la réponse complète avant de parser.
Sur 90 s de latence, l'utilisateur ne voit rien et le watchdog lui-même ne peut
rien mesurer d'utile.

**Correctif attendu.** Consommer le flux natif du provider pour :
- horodater le **premier octet** (`llm_call.first_byte_ms`) — c'est la vraie
  mesure de vivacité ;
- alimenter le watchdog du point 1 sans changer le contrat de sortie.

**Fait quand** : `llm_call` porte `first_byte_ms`, et la latence perçue est
attribuable (attente réseau vs génération).

**Pourquoi différé** : chaque provider expose un flux différent. À faire
provider par provider, pas dans le chemin commun.

---

## 3. `thinking_level` / `reasoning_effort` (C3)

**État actuel.** `core/llm_policy.py` déclare déjà `thinking_level` et
`reasoning_effort` dans `CallProfile`, avec les bornes et la validation. Ils
sont **inertes** : aucun provider ne les lit encore.

**Correctif attendu.**
1. l'hôte les déclare dans son manifeste (`llm_policy.profiles.<classe>`) ;
2. `Llm._apply_call_policy` les transmet au provider ;
3. chaque provider les traduit dans son vocabulaire natif, ou les ignore
   explicitement (jamais un défaut inventé à la place d'une absence).

**Fait quand** : un test montre qu'une valeur déclarée par l'hôte arrive au
provider, qu'une absence de déclaration laisse le provider tel quel, et qu'une
valeur inconnue est ignorée sans erreur.

**Pourquoi différé** : transverse à tous les providers, sans effet mesurable
sur le run `dcc9ec4b`.

---

## 4. Hedging / bascule de fournisseur (C4-bis)

**Cause mesurée.** Une mission a perdu deux tentatives sur un fournisseur muet.

**Règle non négociable.** Aucune rotation aveugle. La bascule n'existe que si
l'hôte a déclaré **explicitement** une politique (`llm_policy.on_stall`), avec :
- la liste ordonnée des fournisseurs de repli ;
- le nombre de tentatives par fournisseur ;
- le budget total partagé (le budget ne double pas parce qu'on change de
  fournisseur).

Sans déclaration : comportement actuel (arrêt honnête, message clair).

**Fait quand** : sans politique déclarée, le code ne bascule jamais (test) ;
avec politique déclarée, la bascule respecte l'ordre et le budget (test).

**Pourquoi différé** : c'est une décision de l'hôte, pas du cerveau. Le P0
garantit déjà qu'une panne ne soit plus imputée au plan.

---

## 5. Parité du manifeste hôte (C5)

**Correction factuelle (15/11).** Le cerveau avait écrit que le plafond venait
du manifeste hôte. C'est faux : l'hôte ne déclarait rien, et notre code
retombait sur le budget **demandé par l'appelant** (600 à l'époque). Les 294
caractères observés venaient de notre règle « champ entier ou omis » : la liste
des fenêtres ne tenait pas, elle était supprimée, et le marqueur ne disait pas
quoi. Corrigé (budget 2000, champs omis nommés). L'hôte déclare désormais
`world_snapshot.max_chars = 2000` : le signal `world_snapshot.budget_low`
redevient ce qu'il doit être — un vrai signal, plus un artefact.

**Reste à vérifier au prochain rejeu :**
- `world_snapshot.chars` > 1200 et aucun `world_snapshot.budget_low` ;
- `llm_policy` accepté dans `metadata` ou `environment` (l'hôte ne déclare rien
  aujourd'hui : nos défauts s'appliquent, c'est le comportement voulu) ;
- aucun nom de cible publié par le monde n'arrive sans être résolvable.

---

## 6. Nommage à l'arrivée (arbitrage du 15/11)

**Décision : option (a), bornée.** Le cerveau projette le snapshot en
`register=True`, mais seulement pour les producteurs que `get_world_state`
déclare **explicitement** (`ref` + `scope`) :

- jamais `well_known` (ce sont des lieux, pas des cibles) ;
- plafonné à `WORLD_SNAPSHOT_MAX_ITEMS` entrées par photo ;
- idempotent par construction : `TargetRegistry.upsert` est indexé par
  corrélation puis par référence, donc la même référence garde le même nom (pas
  de cibles fantômes, pas de générations qui dérivent) ;
- un nom publié par le monde reste **non prouvé** : le gate de preuve s'applique
  à l'identique.

**Prérequis hôte** : que `get_world_state` déclare `focused` et `windows[]`
comme producteurs. Sans déclaration, `project_target_payload` ne fait rien
(comportement actuel, aucun risque).

**Fait quand** : un plan qui référence un handle du monde s'exécute sans
repasser par le sélecteur, et un test montre qu'aucune entrée `well_known` n'est
inscrite.

---

## 7. `enumeration` vu du gate (arbitrage du 15/11)

**Décision : mapper dans `fields.attributes`, borné.** Notre règle « une surface
non énumérable ne prouve rien à elle seule » a besoin de le savoir ; sinon le
vérificateur conclut `uncertain` sur une surface légitimement non énumérable.

Forme attendue : `{window, nodes (compte), enumerable, reason}` — jamais la
liste. Aucun code cerveau à écrire : c'est de la déclaration côté hôte, puis un
banc de non-régression sur le prompt du vérificateur.

---

## 8. Divers — dettes identifiées, non bloquantes

| Dette | Où | Pourquoi ce n'est pas fait |
|---|---|---|
| `TARGET_ERROR_CODES` n'est utilisé nulle part | `core/targets.py:25` | Les codes vivent désormais dans `TargetingResult.error_code`. La constante est morte : soit on la branche comme validation, soit on la supprime. Aucun impact runtime. |
| `render_world_snapshot` projette avant de borner | `core/alignment.py` | C'est le comportement voulu (ne pas gaspiller le prompt en coordonnées). À documenter si un jour une vue « raw bornée » est demandée. |
| Deux formes de bundle d'échec (`dict` / objet) | `core/executor.py`, `core/solver.py` | `_failure_error_code` et `_record_failure` lisent les deux. Unifier demanderait de toucher tous les producteurs d'échec : hors périmètre d'un correctif de stabilité. |

---

## Ordre recommandé

1. **§1 watchdog** — le plus rentable en observabilité pour un risque contenu.
2. **§7 nommage à l'arrivée** — débloque la valeur des correctifs livrés (le
   planificateur cesse de repayer un sélecteur).
3. **§8 `enumeration`** — une déclaration hôte, un banc, aucun risque.
4. **§3 thinking_level** — quand un besoin de qualité par classe apparaît.
5. **§2 streaming** — provider par provider, sans urgence.
6. **§4 hedging** — seulement si l'hôte exprime un besoin explicite.
7. **§5 parité** — vérification au prochain rejeu, plus une action à écrire.

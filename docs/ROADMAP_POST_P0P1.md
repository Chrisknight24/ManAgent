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

**Cause mesurée.** Le plafond effectif du monde venait du manifeste
(`world_snapshot.max_chars` ≈ 300) alors que le cerveau demandait 2000. Le
cerveau a maintenant un signal (`world_snapshot.budget_low`) et une vue
`planner` qui ne gaspille plus le budget en coordonnées, mais l'hôte doit
relever son plafond.

**Correctif attendu.** Message hôte (déjà rédigé, voir
`brain_to_host.md` côté pont) + vérification de parité :
- `world_snapshot.max_chars` ≥ 2000 ;
- `llm_policy` accepté dans `metadata` ou `environment` ;
- les noms de cibles arrivent dans `target_registry` **avant** le premier plan.

**Fait quand** : un run réel produit `world_snapshot.chars` > 1200 et aucun
`world_snapshot.budget_low`.

---

## 6. Divers — dettes identifiées, non bloquantes

| Dette | Où | Pourquoi ce n'est pas fait |
|---|---|---|
| `TARGET_ERROR_CODES` n'est utilisé nulle part | `core/targets.py:25` | Les codes vivent désormais dans `TargetingResult.error_code`. La constante est morte : soit on la branche comme validation, soit on la supprime. Aucun impact runtime. |
| `render_world_snapshot` projette avant de borner | `core/alignment.py` | C'est le comportement voulu (ne pas gaspiller le prompt en coordonnées). À documenter si un jour une vue « raw bornée » est demandée. |
| Deux formes de bundle d'échec (`dict` / objet) | `core/executor.py`, `core/solver.py` | `_failure_error_code` et `_record_failure` lisent les deux. Unifier demanderait de toucher tous les producteurs d'échec : hors périmètre d'un correctif de stabilité. |

---

## Ordre recommandé

1. **§5 parité hôte** — aucun code, débloque la valeur des correctifs déjà
   livrés (le monde utile et les noms de cibles).
2. **§1 watchdog** — le plus rentable en observabilité pour un risque contenu.
3. **§3 thinking_level** — quand un besoin de qualité par classe apparaît.
4. **§2 streaming** — provider par provider, sans urgence.
5. **§4 hedging** — seulement si l'hôte exprime un besoin explicite.

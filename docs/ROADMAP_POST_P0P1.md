# Roadmap résiduelle — après P0/P1 (run `dcc9ec4b`)

Ce document est la mémoire du chantier. Il décrit ce qui **reste**, et garde en
tête de section, quand il est livré, le point qui vient d'être fermé (avec son
banc). Rien ici n'est nécessaire à la suite immédiate : le P0 et le P1 sont
livrés, testés et couverts par des bancs de non-régression.

Référence des correctifs livrés : `docs/CHANTIER_TARGET.md`,
`tests/test_mission_dcc9ec4b_regression.py` et
`tests/fixtures/dcc9ec4b_replay.jsonl` (rejeu doré de la mission).

---

## 1. Watchdog d'appel (C1-bis) — **livré le 15/11**

**Cause mesurée.** Un appel pouvait rester muet jusqu'au plafond de sa classe
(600 s planner/juge, 300 s analyse, 120 s sélecteur) : l'utilisateur ne voyait
rien, et rien ne distinguait « le fournisseur travaille » de « il ne répondra
jamais ».

**Livré.** `core/llm.py` émet `llm_call.stall` (`kind`, `tick`, `elapsed_s`,
`budget_s`, mission/solver/étape quand ils sont connus) tant qu'un appel est en
vol. L'intervalle vit dans `core/llm_policy.py` (`stall_interval_s`, 30 s par
défaut, réglable par l'hôte via `metadata.llm_policy`, 0 = désactivé). Le
battement **n'interrompt rien et ne raccourcit aucun budget** : le profil de la
classe reste la seule autorité. Il couvre aussi le chemin legacy, qui n'avait
aucune surveillance en vol.

**Banc.** `tests/test_llm_stall_watchdog.py` : cadence, absence de battement
quand c'est désactivé ou rapide, budget inchangé, exception préservée,
annulation toujours effective.

**Ce qui reste de ce chantier** : rien. Le streaming (§2) rendra `first_byte_ms`
disponible et le battement plus fin, mais il n'est pas nécessaire à celui-ci.

---

## 2. Streaming des réponses structurées (C2-bis) — **livré le 16/11**

**Cause mesurée.** Le provider attend la réponse complète avant de parser. Sur
90 s de latence, l'utilisateur ne voit rien et le watchdog lui-même ne peut rien
mesurer d'utile.

**Correctif attendu.** Consommer le flux natif du provider pour :
- horodater le **premier octet** (`llm_call.first_byte_ms`) — c'est la vraie
  mesure de vivacité ;
- alimenter le watchdog du point 1 sans changer le contrat de sortie.

**Livré.**
- `providers/sse_transport.py` : **une seule** discipline de clés pour tous les
  flux (rotation 429/503, erreurs typées, annulation) et surtout : aucun rejeu
  silencieux après avoir livré du contenu.
- `providers/openai_compatible.py` : accumulateur SSE JSON (texte, usage,
  premier contenu, lignes illisibles comptées, erreur serveur jamais
  silencieuse). Branché sur OpenAI, OpenRouter, Groq, DeepSeek.
- `providers/anthropic_stream.py` : accumulateur des fragments d'outil forcé
  (`input_json_delta`), avec vérification du nom d'outil. Branché sur Anthropic.
- Gemini : flux SDK (`generate_content_stream`) accumulé puis validé, dans le
  provider (le format n'est pas SSE).
- `BaseProvider` : `STREAMS_STRUCTURED` (capacité déclarée, défaut `False`),
  `reset_first_byte` / `_mark_first_byte`, hook optionnel posé par le cerveau.
- `Llm` : `llm_call.first_byte_ms` seulement si un premier contenu a été
  réellement observé ; sinon absent (aucune valeur inventée).
- Coupe-circuit hôte : `llm_policy.stream_structured` (défaut `true`, `false`
  partout). Un provider qui ne déclare pas la capacité garde son chemin
  classique, à l'octet près.
- Bancs : `tests/test_streaming_structured.py` (famille OpenAI-compatible, un
  cas par provider avec endpoint vérifié) et
  `tests/test_streaming_anthropic_gemini.py` (fragments d'outil, chunks SDK,
  pas de rejeu après contenu, coupe-circuit). Aucune requête réseau réelle.

**Ce qui reste de ce chantier** : rien côté code. La mesure devient utile au
prochain rejeu réel : on pourra distinguer attente réseau et génération.

---

## 3. `thinking_level` / `reasoning_effort` (C3) — **livré le 15/11**

**Cause mesurée.** L'hôte n'avait aucun moyen de demander « réfléchis plus » sur
une classe d'appel précise, alors que tous les fournisseurs exposent un réglage
équivalent sous des noms différents.

**Livré.**
1. `core/llm_policy.py` valide les deux indices dans un vocabulaire **neutre**
   (`minimal`, `low`, `medium`, `high`). Une valeur hors vocabulaire est
   ignorée (log) ; une absence reste une absence, sans défaut inventé.
2. `Llm._apply_call_policy` pose `call_thinking_level` /
   `call_reasoning_effort` sur le provider, comme il posait déjà les délais.
3. `BaseProvider.SUPPORTS_REASONING_HINTS = False` par défaut : un provider qui
   sait traduire les indices passe le drapeau à `True` et lit les attributs.
   Les autres les ignorent **explicitement**, et le cerveau l'écrit (debug) au
   lieu de laisser croire que le réglage a pris.
4. `llm_call` porte les indices réellement demandés, pour que l'observabilité
   dise ce qui a été tenté et non ce qu'on espérait.

**Banc.** `tests/test_llm_reasoning_hints.py` : vocabulaire, valeurs inconnues,
absence, attributs réellement posés, événement `llm_call`, drapeau provider.

**Ce qui reste de ce chantier** : brancher la traduction native provider par
provider, quand un besoin réel apparaît. Le contrat, lui, est stable : aucun
provider n'est obligé de comprendre les indices.

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
quoi. Corrigé (champs omis nommés). L'hôte déclare désormais
`world_snapshot.max_chars = 2000` : le signal `world_snapshot.budget_low`
redevient ce qu'il doit être — un vrai signal, plus un artefact.

**Budget configurable (livré le 15/11).** Il n'était pas seulement écrit en
dur : la valeur de l'hôte **remplaçait** la nôtre dans les deux sens
(`limit = int(cfg.get("max_chars") or max_chars)`). Demander 600 à un hôte qui
déclare 2000 donnait donc 2000 — le réglage était décoratif. Désormais
`min(demande, plafond hôte)` : l'hôte ne peut que **réduire**. Le budget se
règle par payload (`world_snapshot.max_chars`, racine ou
`runtime_configuration`) ou par environnement (`MANAGENT_WORLD_SNAPSHOT_CHARS`),
défaut **2000**, borne absolue 4000. Un seul réglage pour tous les points de
lecture ; la convergence finale demande explicitement le maximum.
L'événement `world_snapshot` porte maintenant `budget` : un run court n'est plus
automatiquement un hôte pauvre, c'est peut-être un réglage bas.

**Reste à vérifier au prochain rejeu :**
- `world_snapshot.chars` > 1200 et aucun `world_snapshot.budget_low` ;
- `llm_policy` accepté dans `metadata` ou `environment` (l'hôte ne déclare rien
  aujourd'hui : nos défauts s'appliquent, c'est le comportement voulu) ;
- aucun nom de cible publié par le monde n'arrive sans être résolvable.

---

## 6. Nommage à l'arrivée (arbitrage du 15/11, corrigé le même jour)

**Décision : rien à faire côté cerveau.** L'option (a) que nous avions retenue
— inscrire les producteurs du snapshot — est **retirée**. Deux raisons :

1. elle contredit notre propre décision (`docs/HOST_CONTRACT.md` §3g.1) : la
   photo du monde reste **descriptive** (`register=False`), aucune adoption
   implicite, parce qu'une photo peut être périmée entre la lecture et l'action ;
2. elle aurait produit des noms acceptés par le validateur (`registry.all()`
   alimente `_known_target_local_names`) puis **refusés par les acteurs** de
   l'hôte, qui ne savent résoudre que les références de leur magasin. On aurait
   échangé un tour de sélecteur contre un échec d'action.

La voie propre est celle de l'hôte : un producteur explicite qui **matérialise**
une vraie cible en un appel (fenêtre, focus). C'est notre modèle habituel
(`produces`/`consumes` + `output_target_name`) : le nom est actionnable, et le
coût est payé une fois au lieu d'à chaque photo.

**Ce qu'il faut** : que l'hôte expose ce producteur. Aucun code cerveau à écrire
d'ici là, et sans lui rien ne change.

---

## 7. Preuve bornée / `enumeration` (arbitrage du 15/11)

**Livré côté hôte** : `enumeration = {window, nodes (compte), truncated?,
reason}` dans `fields.attributes`, et `invariants.enumerable` **conservé** avec
son sens propre (« cette cible s'appuie sur l'arbre »), qui n'est pas le nôtre
(« la surface est-elle entièrement couverte »). Les deux questions sont
distinctes et les deux faits sont utiles : **aucune clé à déplacer**.

**Livré côté cerveau** : la règle 2 du prompt du vérificateur couvre désormais
une passe **bornée** (compte plancher, couverture partielle, échantillon), et
distingue une passe qui n'a **rien trouvé** (absence de mesure → affaiblit la
provenance sans limiter la preuve) d'une couverture partielle (→ limite la
preuve). Le wording décrit la sémantique du fait, sans citer une clé d'hôte :
`tests/test_target_verification_bounded_proof.py`.

**Reste à vérifier au rejeu :**
- `enumeration` et `invariants` apparaissent bien dans `target_json` : ils
  dépendent de la déclaration `fields.attributes` de l'hôte. Sans elle, le fait
  existe chez lui et reste invisible chez nous ;
- `truncated: true` avec un compte très inférieur au plafond annoncé (200) :
  soit le compte est un plancher, soit `truncated` veut dire autre chose. À
  trancher avec l'hôte, car c'est ce fait qui déclenche la règle ;
- `uia_no_cover` (pas de couverture) et `uia_no_node` (rien trouvé) ne doivent
  pas produire le même verdict.

**Lecture au rejeu (outil).** `tools/analyze_replay.py` imprime désormais une
section « vitalité » à côté de la chaîne d'ancrage : nombre d'appels et
d'échecs, durée max/médiane, premier contenu mesuré (min/médiane/max), nombre de
battements d'attente, snapshots du monde (`chars`, `budget`, `budget_low`) et
alertes explicites (`AUCUN FIRST BYTE`, `APPELS EN ECHEC`, `BUDGET BAS`,
`SNAPSHOT COURT ?`, `MESURE INCOHERENTE`). La même lecture est disponible en
`--json` (`vitality`). C'est l'outil qui tranchera §5 et §7 sur une mission
fraîche, sans juger le métier : il ouvre une question, il ne condamne pas.

---

## 8. Divers — dettes identifiées, non bloquantes

| Dette | Où | Pourquoi ce n'est pas fait |
|---|---|---|
| `TARGET_ERROR_CODES` n'est utilisé nulle part | `core/targets.py:25` | Les codes vivent désormais dans `TargetingResult.error_code`. La constante est morte : soit on la branche comme validation, soit on la supprime. Aucun impact runtime. |
| `Llm.stream` est mort | `core/llm.py:624` | Il appelle `provider.stream`, qui n'existe pas (les providers exposent `stream_response`) et aucun appelant du dépôt ne l'utilise. Le supprimer ou le rebrancher est un chantier séparé, avec banc ; le laisser en l'état ne change rien au runtime. |
| `render_world_snapshot` projette avant de borner | `core/alignment.py` | C'est le comportement voulu (ne pas gaspiller le prompt en coordonnées). À documenter si un jour une vue « raw bornée » est demandée. |
| Deux formes de bundle d'échec (`dict` / objet) | `core/executor.py`, `core/solver.py` | `_failure_error_code` et `_record_failure` lisent les deux. Unifier demanderait de toucher tous les producteurs d'échec : hors périmètre d'un correctif de stabilité. |

---

## Ordre recommandé

1. **§5 / §7 vérifications de rejeu** — aucun code : on lit les événements et on
   tranche ce qui reste (`budget`, `enumeration`, `invariants` visibles,
   `first_byte_ms`).
2. **§4 hedging** — seulement si l'hôte exprime un besoin explicite.
3. **§6 nommage** — en attente d'un producteur côté hôte ; rien à faire ici.
4. **Rejeu réel** — dès que l'hôte a rebuild et une mission fraîche.

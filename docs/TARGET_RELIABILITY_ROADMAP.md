# TargetReliability — Roadmap d'implémentation

**Statut : implémenté le 2026-11-09 (phases 0→6).** Cette roadmap reste la
mémoire du chantier « fiabilité des cibles ». Elle complète `TARGET_ROADMAP.md`
(registre de cibles) sans le remplacer.

- Contrat hôte : [`HOST_CONTRACT.md`](HOST_CONTRACT.md) (section cibles)
- Code cible : `core/target_contract.py`, `core/targeting.py`, `core/targets.py`,
  `tools/internal_tools.py`, `core/executor.py`, `core/solver.py`,
  `core/runtime_state.py`, `core/orchestrator.py`
- Prompts : `prompts/{base,fr,en}/target_verification.md`,
  `target_selection.md`, `convergence.md`, `planner.md`

---

## 1. Problème à résoudre

Un sélecteur de cible peut « tâtonner » et proposer une cible plausible mais
fausse. Aujourd'hui la vérification est :

- un **seul** appel LLM ;
- sur les **métadonnées** seulement (`description`, `scope`, `attributes`) ;
- **fail-open** (pas de métadonnées → accepté ; vérificateur en panne →
  accepté ; verdict négatif `confidence=low` → accepté) ;
- **jamais** de vérification post-action déterministe.

Or, pour une cible **image/visuelle**, les métadonnées sont muettes et la
`description` est produite par celui qui tâtonne : le vérificateur ne peut pas
prouver la nature réelle de la cible. Trou béant pour un hôte computer-use
(cliquer le bon bouton), un agent de code (écrire dans le bon fichier), un
robot (saisir le bon objet).

**Objectif** : une vraie mécanique de fiabilité, **agnostique**, activée
seulement pour les cibles que l'hôte déclare « risquées ».

---

## 2. Décisions verrouillées (Christian, 2026-11-09)

1. **Le vérificateur ne se substitue jamais au sélecteur.** S'il juge qu'on
   peut creuser, il **oriente** (`next=zoom`, `prefer_internal` indicatif).
   Le harnais **réinterroge le sélecteur** avec un zoom sur **l'ancienne
   candidate**, la proposition du vérificateur en contexte, et les infos hôte
   habituelles. Tout se passe comme si le sélecteur avait lui-même demandé un
   zoom (mécanisme `probes` déjà compris par l'hôte).
2. **Pas de fallback silencieux.** Après un zoom, si le sélecteur ne trouve
   rien de meilleur :
   - verdict initial `uncertain` → on **accepte l'ancienne candidate avec un
     warning partagé** (fail-open contrôlé) ;
   - verdict initial `mismatch` (confiance medium/high) → **pas de fallback**,
     on refuse / replanifie.
   C'est le harnais qui applique cette règle déterministe ; le sélecteur reste
   maître du choix pendant le tour borné.
3. **Warnings partagés** (non bloquants) : un doute de cible doit atteindre le
   planner (retries) **et** `ConvergenceDecision` (étape + finale), qui peut
   refuser s'il estime que l'action a pu viser la mauvaise cible.
4. **Agnosticisme strict** : aucun nom d'outil, de champ ou d'application d'un
   hôte dans le cerveau ou les prompts génériques. Tout passe par
   `disclosure.reliability`, `disclosure.evidence`, `disclosure.probes`,
   `disclosure.anchors`.
5. **Rétro-compatibilité** : champs optionnels ; absence = comportement actuel.
6. **Ordre de livraison** : coder côté ManAgent d'abord, mettre à jour la doc,
   et **seulement ensuite** contacter la team hôte pour un manifeste.

---

## 3. Modèle cible

### 3.1 Profil de fiabilité (hôte)

```
disclosure.reliability: "high" | "medium" | "low"   # défaut global, optionnel
# surcharge possible : produces[].reliability, anchors[].reliability
```

- `high` : cible structurée/stable (UI, chemin, identifiant) → vérification
  légère ou bypass.
- `medium` : standard (comportement actuel).
- `low` : localisation visuelle/incertaine → vérification approfondie
  systématique.

Absent = `medium` (aucune régression).

### 3.2 Capacités de preuve (hôte)

```
disclosure.evidence: ["metadata", "image", "rect", "nested_targets", "invariants"]
```

Absent = `["metadata"]`. Les capacités ne sont pas des noms d'outils : ce sont
des **types de preuve** que l'hôte sait joindre à une candidate.

### 3.3 Bundle de preuves (cerveau → vérificateur)

```
TargetEvidence {
  description, scope, attributes,   # existant
  image: MediaAsset?,               # crop optionnel (canal média, pas le prompt texte)
  rect: [x, y, w, h]?,              # optionnel
  nested: [ObservedCandidate]?,     # cibles internes optionnelles
  invariants: {...}?                # faits vérifiables déclarés
}
```

### 3.4 Verdict du vérificateur

```
verdict: "match" | "mismatch" | "uncertain"
confidence: "low" | "medium" | "high"
prefer_internal: <clé locale>?      # indicateur, PAS un choix
reason: phrase courte
next: "accept" | "zoom" | "replan"
```

`matches` (bool) reste rempli pour compatibilité (= `verdict == "match"`).

### 3.5 Zoom implicite et « creuser »

- Un `disclosure.probes[]` peut déjà viser une candidate locale
  (`subject.kind="local"`) : c'est le zoom sur l'ancienne candidate.
- Le harnais auto-déclenche ce zoom sur `low` + (`uncertain`|`mismatch`).
- Si la probe renvoie des cibles internes (`produces` inclut `targets`), le
  sélecteur est réinterrogé avec :
  - l'ancienne candidate comme sujet ;
  - les nouvelles candidates produites ;
  - `verifier_hint` (proposition `prefer_internal` + `reason`).
- Borne stricte : **1 zoom + 1 re-sélection**.

### 3.6 Politique par fiabilité

| Fiabilité | Preuves | Zoom auto | Fallback |
|---|---|---|---|
| `high` | celles que l'hôte déclare (`evidence`) | sur `mismatch` explicite | warning si `uncertain` |
| `medium` | idem | sur `mismatch` explicite ou `prefer_internal` | warning si `uncertain` |
| `low` | idem | oui (dès qu'un `zoom_probe` est déclaré) | warning si `uncertain`, refus si `mismatch` |

La fiabilité ne « saute » jamais la vérification : elle règle la **suite**
(`next`) après un `mismatch`, et le bundle de preuves suit `disclosure.evidence`.
Un hôte peut donc déclarer `evidence` sans changer sa fiabilité, et inversement.

### 3.7 Warnings partagés

Deux canaux, comme l'existant :

- **gap bloquant** (`plan_warnings_by_solver`) : mismatch non résolu → échec
  normal / replan.
- **warning non bloquant** (`target_warnings_by_solver`, plus les canaux
  existants `expected_warnings_by_solver` et `perception_warnings_by_solver`) :
  doute/fail-open. Lu par :
  - planner (retries) ;
  - `ConvergenceDecision` **étape** ;
  - `ConvergenceDecision` **finale**.

---

## 4. Phases d'implémentation

### Phase 0 — Contrat (docs + schéma)
- [x] `core/target_contract.py` : champs `reliability`, `evidence`,
      `zoom_probe` dans `DisclosureSpec` ; surcharges `produces[].reliability`,
      `anchors[].reliability`.
- [x] Parseurs tolérants + warnings sur valeurs inconnues.
- [x] `docs/HOST_CONTRACT.md` : documenter les champs.
- [x] Tests : parsing, valeurs invalides, absence = inchangé.

### Phase 1 — Ne jamais perdre la perception
- [x] `tools/internal_tools.py` : propager `TargetingResult.reason` dans
      `_target_error` (`observed`/`message`).
- [x] Émettre `target.resolve_failed` (code + raison).
- [x] Si aucune raison riche : +1 analyse de repli bornée
      (`_run_llm_analysis`) et remonter `observed`/`gap`/`suggested_targets`.
- [x] Tests : la raison atteint `alignment_failure` + rapport d'échec ; aucun
      candidat injecté dans le prompt planner.

### Phase 2 — Bundle de preuves + vérificateur générique
- [x] `core/targeting.py` : construire `TargetEvidence` selon
      `disclosure.evidence`.
- [x] Nouveau `prompts/{base,fr,en}/target_verification.md` générique
      (metadata + image + rect + nested + invariants).
- [x] Schéma verdict `match|mismatch|uncertain` + `next` + `prefer_internal`.
- [x] Tests : cible image sans métadonnées mais avec crop ; cible UI
      structurée → vérif légère.

### Phase 3 — Politique de fiabilité
- [x] Lire `reliability` (global + surcharges) et choisir la profondeur.
- [x] `high` → léger ; `medium` → standard ; `low` → approfondi.
- [x] Tests : matrice fiabilité × verdict ; aucun nom d'hôte.

### Phase 4 — Zoom implicite + creuser
- [x] Auto-déclencher `zoom_probe` sur `low` + `uncertain|mismatch`.
- [x] Réinterroger le sélecteur avec `verifier_hint` + candidates internes.
- [x] Fallback déterministe (§2.2).
- [x] Tests : zoom sur ancienne candidate (pas de relecture plein écran) ;
      sélection interne ; budget épuisé → replan.

### Phase 5 — Warnings partagés
- [x] Émettre un warning sur `uncertain`/fail-open.
- [x] Injecter dans `previous_failures` (planner retries).
- [x] Injecter dans `ConvergenceDecision` **étape**
      (`executor._evaluate_semantic_convergence`).
- [x] Vérifier `ConvergenceDecision` **finale** (déjà câblée).
- [x] Tests : warning visible planner + convergence + HTML.

### Phase 6 — Observabilité + i18n + non-régression
- [x] Événements `target.resolve_failed`, `target.verification`
      (verdict, fiabilité, next).
- [x] Rendu HTML (fil d'exécution).
- [x] Nouvelles chaînes i18n fr/en + `compile_i18n --check`.
- [x] Suite complète.

---

## 5. Garanties d'agnosticité

- Le cerveau ne lit que : `reliability`, `evidence`, `anchors`, `probes`,
  `invariants`. Aucune sémantique d'hôte.
- Les preuves passent par le canal média déclaré, jamais en base64 dans le
  prompt texte.
- Les champs sont optionnels : absence = comportement actuel.
- Aucun seuil codé pour un monde particulier : la politique suit le profil
  déclaré.

---

## 6. Réserves / points à ne pas oublier

- **Le vérificateur n'élit pas** : il oriente, le sélecteur choisit.
- **Pas de fallback silencieux** : warning si `uncertain`, refus si `mismatch`.
- **Borne** : 1 zoom + 1 re-sélection, jamais de boucle.
- **Convergence étape** : corrigée en Phase 5 (`world_warnings` +
  `expected_warnings`).
- **Team hôte** : à contacter **après** que notre côté soit propre (schéma +
  doc + tests verts), pas avant.
- **Ne jamais négliger les rapports de perception** : la raison riche est une
  donnée de première classe (Phase 1).

---

## 7. Critères de sortie

- Suite complète verte.
- `compile_i18n --check` OK.
- Un test de bout en bout simule : cible `low` + `uncertain` → warning visible
  côté planner et convergence ; cible `low` + `mismatch` → zoom sur l'ancienne
  candidate → sélection interne ; aucune régression sur les hôtes sans
  `disclosure`.

---

## 8. Journal d'implémentation (2026-11-09)

- Contrat : `core/target_contract.py` (`RELIABILITY_VALUES`, `EVIDENCE_VALUES`,
  `_parse_reliability`, `_parse_evidence`, `DisclosureSpec.reliability/evidence/zoom_probe`,
  surcharges `ProducerSpec.reliability` / `AnchorSpec.reliability`).
- Vérificateur : `core/targeting.py` (`VerificationOutcome`,
  `TargetVerification.verdict/next/prefer_internal`, `effective_reliability`,
  `build_target_evidence`, `_derive_next`, boucle `resolve_target` avec zoom
  implicite borné et `uncertain` accepté + warning).
- Perception : `tools/internal_tools.py` (`_target_error` enrichi,
  `_enrich_failed_target_error`, `_emit_target_resolve_failed`,
  `_store_target_warnings` → `target_warnings_by_solver` + `target.verification`).
- Warnings partagés : `core/runtime_state.py`,
  `core/executor.py::_evaluate_semantic_convergence`,
  `core/solver.py` (juge final + `previous_failures` planner).
- Observabilité : `tools/build_observability_report.py`
  (`target.verification`, `target.resolve_failed` dans le fil d'exécution).
- Prompts : `prompts/{base,fr,en}/target_verification.md` et
  `target_selection.md` (bloc `verifier_hint`, preuves déclarées, verdict).
- Tests : `tests/test_target_reliability.py` (13 cas).
- Validation : **875 passed**, `compile_i18n --check` → **670 chaînes**.
- Reste à faire **côté hôte uniquement** : déclarer `disclosure.reliability`,
  `disclosure.evidence` et `disclosure.zoom_probe` dans son manifeste s'il veut
  activer la vérification approfondie. Aucune modification de notre code n'est
  requise pour un hôte qui n'ajoute rien (comportement historique inchangé).

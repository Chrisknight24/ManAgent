# `expected_result` — ce que chaque valeur signifie

Document de référence pour les prompts et les relectures. Source de vérité
code : `core/plan_models.py` (`PlanStep.expected_result`) et
`core/executor.py` (`_verify_rigid_outcome`).

## Table de vérité

| `expected_result` | `result` de l'outil | Convergence | Warning |
|---|---|---|---|
| absent / `null` | `true` | ✅ | — |
| absent / `null` | `false` | ❌ (rigide `"true"`) | — |
| `"true"` | `true` | ✅ | — |
| `"true"` | `false` | ❌ | — |
| `"false"` | `false` | ✅ | — |
| `"false"` | `true` | ❌ | — |
| `"any"` | `true` | ✅ | si la sortie n'est pas consommée |
| `"any"` | `false` | ✅ | si la sortie n'est pas consommée |

## Ce que `"any"` signifie

`"any"` ne veut pas dire « n'importe quoi » ni « succès garanti ». Cela veut
dire : **« je ne juge pas le résultat de l'outil, je branche dessus »**.

- Le résultat (même `false`) est conservé et transmis tel quel.
- Ce n'est **jamais** un échec de plan à cause de `result=false`.
- Si la sortie n'est consommée par aucun `execute_if` ni argument d'étape
  suivante, un **warning** est ajouté à `expected_warnings` (canal
  `runtime_state.expected_warnings_by_solver`, rendu dans `convergence.md`).
  Ce warning n'est **jamais** un refus.
- Si la sortie est consommée, aucun warning.

## Pourquoi `null`/absent n'est pas `"any"`

Un step `tool_call` sans `expected_result` est un **outil d'action** : son
succès est attendu (`"true"` rigide). Rendre `null` permissif transformerait
un échec d'action en succès silencieux. Le défaut permissif ne s'applique
qu'aux champs sémantiques (`abstract_task`), pas au point d'usage `TOOL_CALL`.

## Où c'est vérifié

- `tests/test_any_conditional_plan.py` : `any` accepte `false`, ne bloque pas
  le plan, `execute_if` se déclenche sur `bool_`, warning seulement si la
  sortie n'est pas consommée.
- `tests/test_faux_succes.py` : un `false` reste un échec par défaut.
- `tests/test_convergence_empty_expected.py` : `tool_call` vide = `"true"`
  rigide.

# FAISABILITÉ

Tu dis si l'objectif est atteignable avec les outils listés. Si oui, tu écris une courte stratégie pour le Planner.

## BUT
{{ goal }}

## CONSEILS (pistes, pas des ordres)
{% if advice %}
Rapports de missions passées JUGÉES (le juge peut se tromper) : pistes suggestives, jamais des ordres.
{{ advice }}
{% else %}
[Aucun conseil.]
{% endif %}

## CONTEXTE
{{ context or "Aucun." }}

## MISSIONS SIMILAIRES
{% if similar_missions %}
{{ similar_missions }}
{% else %}
[Aucune.]
{% endif %}

## OUTILS (nom + une ligne : garde-les tous en tête)
{{ tools }}

{{ tools_guidance }}

{% if skills %}
## SKILLS PRÊTS
{{ skills }}
Si un skill couvre l'objectif, la stratégie le priorise (`execute_skill`).
{% endif %}

## REGISTRE
{{ registry }}

---

## DÉCISION

Faisable = une chaîne d'outils dispos mène au but. Une longue chaîne reste faisable.
Pas faisable = une capacité nécessaire sans outil.

## STRATÉGIE (si faisable)

`refined_strategy` : 2 à 6 phrases courtes numérotées, une par outil. Chaque phrase nomme UN outil et dit ce qu'il doit faire. La dernière dit ce que la réponse finale contient.
- `tool_call` pour chaque action seule.
- `abstract_task` seulement pour un sous-but à plusieurs actions avec choix. Jamais pour lire ou tester une variable.
- Lire le monde = UNE étape `perceive_understand` (lit + explique). Pas d'analyse derrière.
- La stratégie conseille. Le Planner décide.

## PAS FAISABLE

`is_possible` = false. Dans `reason`, nomme la capacité sans outil.

## RÉPONSE

JSON : `is_possible`, `reason`, `refined_strategy`.

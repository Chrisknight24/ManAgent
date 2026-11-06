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
{% if world_snapshot %}
## ÉTAT ACTUEL (photo par défaut — contexte seulement, jamais une preuve)
{{ world_snapshot }}
{% endif %}

## MISSIONS SIMILAIRES
{% if similar_missions %}
{{ similar_missions }}
{% else %}
[Aucune.]
{% endif %}

## OUTILS (nom + une ligne : garde-les tous en tête)
{{ tools }}

{{ tools_guidance }}

Les outils hôte sont des **sources de données brutes** : leur nom ou leur description ne dit pas ce qu'il faut en conclure. Seul `perceive_understand` interprète un résultat dans le contexte de la mission. Une stratégie qui lit le monde passe par lui, jamais par un outil de perception appelé en direct.

{% if skills %}
## SKILLS PRÊTS
{{ skills }}
Si un skill couvre l'objectif, la stratégie le priorise (`execute_skill`).
{% endif %}

## REGISTRE
{{ registry }}
{% if targets_enabled %}
## CIBLES
- Les cibles sont des noms locaux (`$@_<nom>`), jamais des références brutes.
- Toute action qui consomme une cible doit recevoir un nom local produit AVANT elle : prévois la perception/producteur de cible, puis l'action.
- N'utilise pas un champ legacy (`cell`, `uia`, coordonnées) à la place d'un paramètre cible requis.
- Une stratégie peut réutiliser une cible déjà nommée dans un argument consommateur.
{% if target_selection_enabled %}
- Plusieurs candidates : prévoir le méta-outil de sélection, qui choisit une clé locale.
{% endif %}
{% endif %}

---

## DÉCISION

Faisable = une chaîne d'outils dispos mène au but. Une longue chaîne reste faisable.
Pas faisable = une capacité nécessaire sans outil.

## STRATÉGIE (si faisable)

`refined_strategy` : 2 à 6 phrases courtes numérotées, une par outil. Chaque phrase nomme UN outil et dit ce qu'il doit faire. La dernière dit ce que la réponse finale contient.
- Sous-buts TOUS différents entre eux et du BUT : jamais le BUT recopié comme sous-but (boucle infinie garantie sinon).
- `tool_call` pour chaque action seule.
- `abstract_task` seulement pour un sous-but à plusieurs actions avec choix. Jamais pour lire ou tester une variable.
{% if world_guidance is not defined or world_guidance %}- Lire le monde = UNE étape `perceive_understand` (lit + explique). Pas d'analyse derrière.
{% endif %}- Réparer le monde, pas le relire : si la perception montre que l'élément manque, la suite agit pour corriger.
- La stratégie conseille. Le Planner décide.

## PAS FAISABLE

`is_possible` = false. Dans `reason`, nomme la capacité sans outil.

## RÉPONSE

JSON : `is_possible`, `reason`, `refined_strategy`.

# RULES — Critères de conformité pour la validation finale des plans

Ce fichier est lu par l'Orchestrateur (`Orchestrator._load_rules_md`) et injecté
tel quel dans le prompt de validation (`plan_validation.md`). C'est un texte en
langage naturel, pas un format structuré à parser — le LLM Judge le lit comme
un humain le lirait. Modifiez-le librement ; aucune section n'est interprétée
par du code.

**⚠️ Brouillon de démarrage** — les critères ci-dessous sont des exemples
plausibles pour amorcer la discussion, pas une politique validée. À réviser
avant mise en usage réel.

---

## 1. Actions nécessitant une confirmation humaine systématique

Le plan doit être marqué `requires_human_confirmation = true` si une étape,
directement ou indirectement, correspond à l'un des cas suivants :

- Suppression de fichiers ou de données, sans étape de sauvegarde préalable
  dans le même plan.
- Envoi de données vers l'extérieur (email, requête réseau, publication)
  contenant des informations potentiellement sensibles.
- Toute action explicitement déclarée `is_irreversible: true` par le Planner
  sur une étape (`PlanStep.is_irreversible`), SAUF si le contexte de la
  mission indique que l'utilisateur a déjà explicitement demandé et confirmé
  cette action précise dans son message d'origine.
- Fermeture d'applications ou de processus susceptibles de contenir un
  travail non sauvegardé, sans vérification préalable de l'état de
  sauvegarde.

## 2. Rejet systématique (is_conformant = false)

- Un plan dont une étape `tool_call` n'a pas de `expected_result` cohérent
  avec son `output_variable_name` déclaré.
- Un plan qui répète EXACTEMENT (même structure) une tentative précédente
  déjà en échec pour ce Solver, SANS justification explicite dans sa
  description de ce qui a changé. (Le signal de détection de ce cas est
  fourni automatiquement dans le prompt — voir `pattern_warning`.)
- Un plan qui, au vu de l'historique compact de la mission fourni plus bas,
  répète un sous-objectif déjà tenté et déjà en échec au même niveau, sans
  rien changer d'utile.
- Un plan dont l'objectif déclaré (`goal`) diverge manifestement de
  l'objectif cible transmis (`{{ goal }}` dans ce prompt).

## 3. Niveaux de risque (risk_level)

- `low` : lecture seule, actions cosmétiques, actions déjà confirmées par
  l'utilisateur dans sa demande initiale.
- `medium` : modification de données récupérables (fichiers avec versioning,
  actions annulables via une action symétrique connue).
- `critical` : toute action couverte par la section 1, ou tout effet de bord
  sur un système/processus extérieur à l'environnement de test.

## 4. Ce que l'Orchestrateur NE doit PAS faire

- Ne pas rejeter un plan uniquement parce qu'il est long ou comporte
  plusieurs étapes — la complexité n'est pas, en soi, un critère de risque.
- Ne pas inventer une irréversibilité non plausible pour une action
  manifestement bénigne (ex : lire un fichier, afficher une fenêtre).
- Ne pas exiger de confirmation humaine pour une action déjà explicitement
  et précisément demandée par l'utilisateur dans son message d'origine
  (redemander confirmation pour ce que l'utilisateur vient de demander
  dégrade l'expérience sans ajouter de sécurité réelle).
- **Ne JAMAIS juger deux étapes "contradictoires" sans regarder leurs
  conditions `execute_if` d'abord.** Chaque étape du plan peut porter une
  condition `[SI ...]` dans le résumé qui vous est fourni. Deux étapes qui
  semblent s'opposer (ex : "envoyer Alt+F4" et "confirmer la fermeture
  directe") sont très souvent des BRANCHES MUTUELLEMENT EXCLUSIVES d'un même
  scénario conditionnel (l'une s'exécute si une variable vaut `True`,
  l'autre si elle vaut `False`) — ce n'est PAS une contradiction, c'est de la
  logique conditionnelle normale. Une étape sans condition `[SI ...]`
  s'exécute toujours ; une étape avec condition ne s'exécute QUE si la
  condition est vraie au moment venu. Ne refusez un plan pour incohérence
  entre étapes que si elles s'exécuteraient réellement EN MÊME TEMPS (aucune
  condition, ou conditions non exclusives) et se contrediraient alors
  vraiment.
- **Ne jamais rejeter un plan pour "incomplétude" par rapport à un objectif
  plus large que celui affiché ci-dessus dans `{{ goal }}`.** Un plan
  proposé par un sous-Solver (issu d'un `abstract_task`) a un mandat
  volontairement restreint — juger ce plan à l'aune de la mission globale
  le fait paraître "incomplet" à tort. Jugez UNIQUEMENT si ce plan atteint
  `{{ goal }}` tel qu'écrit.
- Ne pas confondre une décomposition légitime (plusieurs sous-tâches qui se
  ressemblent en surface mais progressent chacune vers un résultat concret)
  avec une récursion dégénérée (le même sous-objectif réessayé sans rien
  changer après un échec déjà constaté). L'historique compact fourni plus
  bas donne les faits pour trancher — pas une similarité de surface.

Tu analyses l'échec d'une entité précise d'un moteur agentique.

ENTITÉ RESPONSABLE : {{ entity_type }}
RÔLE DE CETTE ENTITÉ : {{ role_description }}

OBJECTIF DE LA SOUS-TÂCHE : {{ goal }}
TYPE D'ERREUR : {{ failure_class }}
DÉTAIL DE L'ERREUR : {{ failure_reason }}

SÉQUENCE D'EXÉCUTION (prunée) :
{{ pruned_attempt }}

Instructions :
1. Identifie un mot-clé de scope STABLE et étroit qui résume la SITUATION précise à l'origine de l'échec (ex: 'keyboard_run_dialog_focus_loss'). Ce n'est pas forcément une application.
2. Propose 5 à 8 mots-clés LARGES et variés (applications, actions, synonymes, outils impliqués) qui permettront de retrouver cette leçon depuis un but de mission différent.
3. Produis une règle impérative courte (1-2 phrases), adressée directement à {{ entity_type }}, pour ÉVITER cette erreur à l'avenir compte tenu de son rôle ci-dessus.
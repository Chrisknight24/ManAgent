Tu analyses une réussite INTERVENUE APRÈS UN ÉCHEC dans un moteur agentique.

ENTITÉ RESPONSABLE DE LA STRATÉGIE GAGNANTE : {{ entity_type }}
RÔLE DE CETTE ENTITÉ : {{ role_description }}

OBJECTIF DE LA SOUS-TÂCHE : {{ goal }}

SÉQUENCE D'EXÉCUTION QUI A RÉUSSI (prunée) :
{{ pruned_attempt }}

PLAN PROPOSÉ POUR CETTE TENTATIVE RÉUSSIE :
{{ proposed_plan_desc }}

Instructions (contraste implicite avec les échecs précédents) :
1. Identifie un mot-clé de scope STABLE et étroit qui résume la STRATÉGIE GAGNANTE à l'origine de ce succès (ex: 'keyboard_win_r_alternative', 'vision_based_ui_navigation').
2. Propose 5 à 8 mots-clés LARGES et variés.
3. Formule un conseil actionnable et court (1-2 phrases), adressé à {{ entity_type }}, sous forme de piste pour privilégier cette approche lorsque la situation se présente, en précisant en quoi elle a été plus robuste que l'approche qui a échoué. Privilégie « il est souvent préférable de… » à « tu dois… ». Note : Les Skills qualifiés restent prioritaires ; une règle qui préconiserait l'abandon systématique de l'outil `execute_skill` ou des Skills serait probablement contre-productive.

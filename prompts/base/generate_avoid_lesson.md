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
3. Formule un conseil actionnable et court (1-2 phrases), adressé à {{ entity_type }}, sous forme de piste pour éviter ce type d'erreur à l'avenir, en tenant compte de son rôle ci-dessus. Privilégie des formulations comme « il est souvent préférable de… » ou « une piste consiste à… » plutôt que « tu dois… ». Note : Les Skills s'auto-guérissent via leur propre registre (SkillRegistry) ; une règle qui interdirait ou découragerait l'usage général de l'outil `execute_skill` ou des Skills serait probablement contre-productive.
4. ANTI-ACHARNEMENT : si l'échec vient d'une ABSENCE d'outils ou de skills (rien de disponible dans l'environnement), une piste utile est de signaler le manque et de s'arrêter rapidement plutôt que d'insister. À l'inverse, une règle absolue du type « ne jamais refuser » serait probablement excessive.

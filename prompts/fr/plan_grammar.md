## GRAMMAIRE DES ÉTAPES

Chaque étape N produit automatiquement deux canaux :
- `$@_bool_step_N` : True si l'étape a tourné sans erreur, False sinon.
- `$@_data_step_N` : le texte / les données renvoyées.

### Variables
- Tu ne cites que des noms AUTO (`$@_data_step_N`, `$@_bool_step_N`) ou du REGISTRE.
- `output_variable_name` (ex `data_résultat`) crée `$@_bool_<racine>` et `$@_data_<racine>`. Ne double jamais le préfixe (`$@_bool_bool_...` interdit).
- Une étape n'utilise que des variables d'étapes ANTÉRIEURES.
- `output_variable_name` ne va JAMAIS dans `tool_args_json` (champ de premier niveau seulement).
- Recopie EXACTEMENT les noms ; n'invente jamais de suffixe.
{% if targets_enabled %}
- `output_target_name` nomme une cible produite par l'étape (sans préfixe `data_`/`bool_`). Tu la réutilises comme `$@_<nom>` uniquement dans un argument consommateur de cible.
- Jamais de référence brute, d'identifiant hôte ni de clé inventée.
{% endif %}

### Conditions (`execute_if`) — deux formes seulement
- `$@_bool_step_N == True` (ou `== False`) : l'étape N a tourné (ou raté).
- `$@_data_step_N == "texte exact"` (ou `!=`) : ce qu'elle a renvoyé. Pour brancher sur un fait du monde, lis-le d'abord via `perceive_understand` (`format_response="yes or no"`), puis teste `== "yes"` / `== "no"`.
- `and` / `or` autorisés. INTERDIT : `.result`, `.data`, `IN`, `CONTAINS`, fonctions.

### Champs avancés d'une étape
- `is_crucial: true` : met la donnée en avant dans le RUM (Registre Utile de Mission). À réserver à la donnée qui RÉPOND à la question (résultat, valeur, confirmation finale), jamais aux artefacts intermédiaires.
- `should_world_state_change_after_action: true` : tu attends un changement visible après l'action (sinon absent / `false`).
- `verify_with` : nom d'un outil `[perception]` pour vérifier un effet subtil (défaut `get_world_state`).
- `waiting_ms` : 0..30000 ms. Absent = défaut appliqué (hôte déclaré, sinon 3000 ms). AVANT si lecture, APRÈS si action.
- `execute_skill` : uniquement si un skill correspondant est listé. Tous ses paramètres requis doivent être renseignés (`@$_param_...`, jamais vide).

### Analyser une donnée ou une image
`llm_analyze_data` ne traite QU'UNE variable déjà produite (`source: "$@_data_xxx"`). Il n'existe pas de source magique (`current_screen`). Pour du visuel : d'abord un outil de capture → `output_variable_name`, puis passe `$@_data_...` à `llm_analyze_data` (image déclarée : `$@_data_<étape>_<champ>`). Jamais d'`abstract_task` pour inspecter une variable : `tool_call` direct.

### Checklist (avant de répondre)
- [ ] Chaque `tool_call` a un `tool_name` exact et non vide.
- [ ] Chaque `$@_...` vient d'une étape antérieure (ou du registre) et est recopié exactement.
- [ ] Chaque `execute_if` suit une des deux formes.
- [ ] Pas d'`abstract_task` pour inspecter une variable.
- [ ] Aucun `output_variable_name` dans `tool_args_json`.
- [ ] Dernière étape = `direct_answer`.

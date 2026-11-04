### Checklist (avant de répondre)
- [ ] Chaque `tool_call` a un `tool_name` exact et non vide.
- [ ] Chaque `$@_...` vient d'une étape antérieure (ou du registre) et est recopié exactement.
- [ ] Chaque `execute_if` suit une des deux formes.
- [ ] Pas d'`abstract_task` pour inspecter une variable.
- [ ] Aucun `output_variable_name` dans `tool_args_json`.
- [ ] Dernière étape = `direct_answer`.

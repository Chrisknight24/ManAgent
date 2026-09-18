# AGENT.md — Règles de travail avec le mainteneur

## 1. Architecture de décision (obligatoire, style ManAgent)
- L'agent propose TOUJOURS un plan de mission AVANT d'agir.
- Le plan liste : objectif, fichiers touchés, commandes prévues, risques.
- Le mainteneur (Christian) doit valider avec `GO`.
- Sans `GO` explicite : NE RIEN MODIFIER, NE RIEN COMMITTER, NE RIEN PUSHER.
- Une seule mission à la fois. Pas d'actions cachées.

Format attendu :
```
MISSION : <nom court>
OBJECTIF : <1 phrase>
PLAN :
1. <fichier/commande> — <pourquoi>
2. ...
RISQUES : <ce qui peut casser>
GO REQUIS : oui
```

## 2. Pédagogie (le mainteneur est nouveau sur git)
- Expliquer simplement, phrases courtes.
- Mot technique (EN) + traduction (FR) la première fois.
  Ex : `commit (enregistrement)`, `branch (branche)`, `cache (mémoire temporaire)`, `clone (copie locale)`.
- Après chaque action git, expliquer ce qui a été fait et comment vérifier.

## 3. Habitudes pro open-source
- Ne jamais versionner (1) : `memory.db`, `*.dll`, `*.tar.gz`, `.venv/`, `__pycache__/`, `events.jsonl`.
- `requirements.txt` / `pyproject.toml` doivent refléter les vrais imports.
- Tests : si un test échoue après un changement code, mettre à jour le test OU corriger le code, jamais ignorer.
- Docs avant code pour toute feature visible.

## 4. Langue
- Réponses en français simple et direct.
- Code et docs techniques en anglais quand c'est la convention open-source (README, LICENSE).

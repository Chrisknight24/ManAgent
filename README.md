# ManAgent — Universal Agent Runtime

Event-driven (piloté par événements) agent runtime. Parle JSON via stdin/stdout (entrée/sortie standard), donc utilisable depuis Qt, Web, CLI sans changer le moteur.

`Frontend (Qt / Web / CLI) → JSON Protocol → ManAgent → Providers (OpenAI, Gemini, Groq, DeepSeek, OpenRouter)`

## Features
- Hub & Spoke orchestrator : Planner → Executor → Solver → Validator
- Progressive disclosure, discovery engine, learner, skills engine
- Multi-providers + multi-clés avec fallback
- i18n FR/EN, prompts versionnés dans `prompts/base|fr|en`
- Protocole détaillé : `docs/PROTOCOL.md` ([English](docs/en/PROTOCOL.md))

## Docs
- [EN] English: [HOST_CONTRACT](docs/en/HOST_CONTRACT.md) · [PROTOCOL](docs/en/PROTOCOL.md) · [DEPLOY](docs/en/DEPLOY.md)
- [FR] Français : [HOST_CONTRACT](docs/HOST_CONTRACT.md) · [PROTOCOL](docs/PROTOCOL.md) · [DEPLOY](docs/DEPLOY.md)

## Install
```bash
git clone https://github.com/Chrisknight24/ManAgent.git
cd ManAgent
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
```

Note : `google-genai` est requis pour Gemini (import `google.genai`). Si absent : `pip install google-genai`.

## Run
```bash
python main.py
# le runtime attend des packets JSON sur stdin, répond sur stdout
```

## Test
```bash
pytest -q
```

## Use from Qt host (QProcess) — portable way
Ne mets pas le repo git dans `build/`. Garde-le ailleurs, ex : `Documents/QtProjets/ManAgent/`, et fais pointer ton app Qt vers lui sans chemin en dur (hardcoded path) :

1. Variable d'environnement (recommandé en dev) :
   `MANAGENT_PATH=C:/.../ManAgent/main.py`, `MANAGENT_PYTHON=C:/.../.venv/Scripts/python.exe`
   Ton `QProcess` lit ces variables, avec fallback : `QCoreApplication::applicationDirPath() + "/managent/main.py"`.

2. Déploiement (deploy = copie pour livraison) :
   script `tools/deploy_to_qt.py --dest <qt-build-dir>/managent` qui copie `main.py core/ providers/ prompts/ memory/ transport/ utils/` (sans `memory.db`, sans `.venv`).
   En dev tu travailles dans le vrai repo, en run Qt utilise la copie. Pas de double édition.

3. Contrat stable : ton app Qt ne parle que JSON stdin/stdout (`docs/protocol.md`). Tant que `main.py` + protocole ne changent pas, tu peux déplacer ManAgent où tu veux.

## Layout
- `main.py` : entrée runtime
- `core/` : orchestrateur, planner, executor, discovery, skills
- `providers/` : OpenAI, Gemini, Anthropic, Groq, DeepSeek, OpenRouter
- `prompts/` : base/fr/en
- `tests/` : pytest
- `tools/` : internal tools, observability

## Essayer en 5 minutes (sans clé API, sans coder)

```bash
python examples/minimal_host.py
```
Ce mini-hôte lance ManAgent, se présente avec 2 faux outils, affiche
le catalogue des modèles et les stats mémoire. Ensuite, lisez
`docs/HOST_CONTRACT.md` (§5 : comment renvoyer un résultat d'outil)
et `docs/host.manifest.example.json` (manifeste complet à copier).

## Contributing
Voir `CONTRIBUTING.md`. License MIT.

# ManAgent — Runtime d'agents universel

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Protocol v1](https://img.shields.io/badge/protocol-v1-informational.svg)](docs/PROTOCOL.md)

**ManAgent est un runtime d'agents piloté par événements.** C'est une boîte
noire qui parle JSON sur stdin/stdout : une app Qt, un backend web ou une CLI
peuvent le piloter sans toucher au code Python.

```
Hôte (Qt / Web / CLI) ──JSON──▶  ManAgent  ──▶  Fournisseurs LLM
                                       │
                                       └──▶  outils hôte (perception / action)
```

Une ligne entrante = un paquet JSON. Une ligne sortante = un paquet JSON.
Les logs vont sur stderr, jamais sur stdout.

## Points clés

- Orchestrateur **Hub & Spoke** : Planner → Executor → Solver → Validator.
- **Outils hôte** : l'hôte déclare ses outils de perception et d'action ;
  ManAgent décide, l'hôte exécute. Aucun résultat n'est inventé.
- **Multi-fournisseurs + multi-clés** avec repli : OpenAI, Gemini, Anthropic,
  Groq, DeepSeek, OpenRouter.
- **Boucle d'apprentissage** : découverte, leçons, automatismes (fantôme →
  production → quarantaine), divulgation progressive.
- **i18n FR/EN**, prompts versionnés dans `prompts/base|fr|en`.
- **Contrat JSON stable** : voir [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Démarrage rapide

```bash
git clone https://github.com/Chrisknight24/ManAgent.git
cd ManAgent
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt          # runtime de base
pip install -r requirements-dev.txt      # tests (pytest)
# optionnel : embeddings locaux (torch, ~500 Mo+)
# pip install -r requirements-embeddings.txt
```

Lancer le runtime (il attend des paquets JSON sur stdin) :

```bash
python main.py
# ou, en build figé :
# managent.exe [--data-dir DIR] [--version]
```

## Essayer en 5 minutes (sans clé API, sans coder)

```bash
python examples/minimal_host.py
```

Ce mini-hôte lance ManAgent, déclare deux faux outils, affiche le catalogue des
modèles et les stats mémoire. Ensuite, lire :

- [`docs/HOST_CONTRACT.md`](docs/HOST_CONTRACT.md) — le contrat boîte noire
  ([English](docs/en/HOST_CONTRACT.md)), en particulier §5 (renvoyer un résultat d'outil).
- [`docs/host.manifest.example.json`](docs/host.manifest.example.json) — un
  manifeste complet à copier.
- [`docs/host.manifest.schema.json`](docs/host.manifest.schema.json) — le schéma
  machine.

## Documentation

| Document | Français | English |
|---|---|---|
| Contrat hôte (commencer ici) | [FR](docs/HOST_CONTRACT.md) | [EN](docs/en/HOST_CONTRACT.md) |
| Protocole JSON (référence) | [FR](docs/PROTOCOL.md) | [EN](docs/en/PROTOCOL.md) |
| Déploiement | [FR](docs/DEPLOY.md) | [EN](docs/en/DEPLOY.md) |
| Carte du code | [FR](docs/FILES.md) | — |
| Alignement & effets | [FR](docs/ALIGNMENT.md) | — |
| Construire l'exécutable | [FR](docs/BUILD_EXE.md) | — |
| Registre de cibles (vision) | [FR](docs/TARGET_REGISTRY.md) | — |

Index complet : [`docs/README.md`](docs/README.md).

## Intégration hôte (Qt / QProcess)

Garde le dépôt git **hors** du dossier de build et pointe-le sans chemin en dur :

```text
MANAGENT_PATH=C:/.../ManAgent/main.py
MANAGENT_PYTHON=C:/.../ManAgent/.venv/Scripts/python.exe
```

Ton QProcess lit ces variables avec un repli du type
`QCoreApplication::applicationDirPath() + "/managent/main.py"`. L'hôte ne parle
que JSON sur stdin/stdout : le dépôt peut donc bouger librement.

Checklist Qt : [`docs/HOST_CONTRACT.md` §9](docs/HOST_CONTRACT.md#9-checklist-qprocess-qt).

## Arborescence

| Chemin | Rôle |
|---|---|
| `main.py` | point d'entrée du runtime |
| `core/` | orchestrateur, planner, executor, découverte, automatismes |
| `providers/` | un fichier par fournisseur LLM + routage |
| `embeddings/` | backends d'embeddings lite / local / distant |
| `memory/` | bases SQLite (missions, leçons, sessions, profils) |
| `tools/` | outils internes, build, rapport d'observabilité |
| `transport/` | JSON stdin/stdout (point de bascule WebSocket/HTTP) |
| `prompts/` | prompts versionnés (`base`, `fr`, `en`) |
| `tests/` | suite pytest |
| `docs/` | contrats, protocole, déploiement, architecture |

## Tests

```bash
pytest -q
```

La suite couvre 469 tests. Sous Windows, `vec0.dll` doit se trouver à côté de
`main.py` (il n'est pas versionné) pour charger l'extension vectorielle SQLite.

## Contribuer

Voir [`CONTRIBUTING.md`](CONTRIBUTING.md). Sécurité :
[`SECURITY.md`](SECURITY.md). Historique : [`CHANGELOG.md`](CHANGELOG.md).

## Licence

MIT — voir [`LICENSE`](LICENSE).

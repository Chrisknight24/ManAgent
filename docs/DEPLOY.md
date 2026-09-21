# DEPLOY — Où mettre managent (hôte Qt + dev)

## 1. Principe : binaire vs données

- Binaire (lecture seule) : `managent.exe` + `_internal/` + `vec0.dll`.
- Données (écriture) : `memory.db`, `rules.md` (copie), `observability/`, modèles.
- L'exe choisit ses données ainsi : `--data-dir <dossier>` > `MANAGENT_DATA_DIR` > dossier courant.
- Au premier run il crée le dossier et y copie `rules.md` par défaut.
- `managent.exe --version` affiche la version (ex : `managent 0.1.0`).

## 2. Dev (aujourd'hui)

L'hôte lance le `dist/managent/managent.exe` construit à la main.
Chemin réglable (jamais en dur) : variable `MANAGENT_EXE` + `MANAGENT_DATA_DIR`
vers un dossier de dev (ex : `C:/.../managent-data-dev`).

## 3. Livraison (setup hôte)

- Copier `dist/managent/` (lite) ou `dist/managent-full/` dans
  `<dossier-app>/managent/` à côté de l'exe hôte (remplace `universal_agent_runtime/`).
- Données par défaut : `%APPDATA%/ManAgent/` (passé via `MANAGENT_DATA_DIR`
  ou `--data-dir`). Jamais d'écriture à côté de l'exe (Program Files protégé).
- Lancer : `managent.exe --data-dir "%APPDATA%/ManAgent"`, dossier de travail libre.
- Santé : attendre `runtime.ready` (15 s), puis `runtime.configure`.
- Modèles d'embedding : écran setup → `embeddings.catalog`, téléchargement
  avec progression, voir `HOST_CONTRACT.md` §3b-3c.

## 4. Repo source (nous)

La source ManAgent vit HORS du build Qt : `Documents/QtProjets/ManAgent/`
(clone GitHub). Le dossier `build/` de Qt peut être effacé à tout moment.

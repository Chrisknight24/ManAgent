# EXE_MIGRATION — Passer l'hôte Qt de main.py à managent.exe

Pour l'agent en charge de l'app hôte (ex : AutoCUse `chatmanager.cpp`).
Contrat inchangé : seul le lancement change, tous les JSON restent identiques.

## 1. Ce qui change (3 lignes)

Avant :
```cpp
QString pythonExe = runtimeDir + "/.venv/Scripts/python.exe";
QString script = runtimeDir + "/main.py";
m_Process->start(pythonExe, QStringList() << "-X" << "utf8" << "-u" << script);
```
Après :
```cpp
QString managentExe = appDir + "/managent/managent.exe";  // exe + dossier _internal
m_Process->start(managentExe, QStringList());
```
- Plus de `.venv`, plus de `main.py`, plus de flags Python.
- `setWorkingDirectory()` : dossier contenant `rules.md` + `memory.db` (créé au run).
- Environnement : plus besoin de `PYTHONIOENCODING`/`PYTHONUTF8` (gérés dans l'exe).

## 2. Ce qui ne change PAS

- Lecture stdout ligne par ligne, JSON uniquement, ignorer le reste (déjà fait : `[RAW PYTHON]`).
- `runtime.ready` → envoyer `runtime.configure`. Timeouts 60 s / 5 min inchangés.
- Tous les events écoutés (`tool.requested` avec `call_id`, `plan.generated`, `response.chunk`...).
- `tool.result` avec `call_id`. `chat.send` avec `content` + `forced_provider` + `forced_model`.

## 3. Nouveautés à câbler (quand prêt, pas urgent)

- `runtime.configured` renvoie aussi `embeddings_mode` + `active_embedding_model`.
- Nouvelles actions : `embeddings.catalog` (liste modèles + installés),
  `embeddings.prepare {id, force, set_default}` (télécharge avec progression),
  `embeddings.set_default {id}` (bascule à chaud).
- Ecran setup conseillé : appeler `embeddings.catalog`, afficher tailles + états,
  boutons Télécharger / Par défaut. Progression via `embedding.download_started/finished/error`.

## 4. Ordre conseillé

1.Pointer l'exe local (`dist/managent/managent.exe`), tester tchat simple.
2. Déployer : copier `dist/managent/` près de l'exe Qt (remplace `universal_agent_runtime/`).
3. Plus tard : écran modèles + versions via Releases GitHub.

# HOST_CONTRACT — Comment utiliser ManAgent sans lire son code

ManAgent est une boîte noire (black box = on l'utilise sans voir l'intérieur).
L'hôte (Qt, Web, CLI) ne touche JAMAIS aux `.py`. Il lance un processus, envoie du JSON sur stdin (entrée standard), lit du JSON sur stdout (sortie standard).

Référence complète : `docs/protocol.md`. Ce contrat est le résumé stable pour intégrateur.

## 1. Lancement (launch)

Commande :
```
<MANAGENT_PYTHON> <MANAGENT_PATH> 
# ex dev : C:/.../ManAgent/.venv/Scripts/python.exe C:/.../ManAgent/main.py
```

- `MANAGENT_PATH` (variable d'environnement = réglage externe) : chemin vers `main.py`.
- `MANAGENT_PYTHON` : python du `.venv` de ManAgent. Ne pas utiliser le python système.
- Dossier de travail (working dir) : le dossier ManAgent (pour `prompts/`, `rules.md`).
- Protocole : une ligne = un JSON. Pas de log texte sur stdout, que du JSON.
- Premier signal attendu : `{"type":"event","event":"runtime.ready","payload":{}}` (voir `main.py`, `core/constants.py:32`).

Santé (healthcheck) : si pas de `runtime.ready` en 15s → tuer le QProcess et relancer.

## 2. Paquets (packets)

Requête (hôte → ManAgent), voir `transport/packet_models.py:17` :
```json
{"id":"req_001","type":"request","action":"chat.send","payload":{"message":"Bonjour"}}
```

Réponse (ManAgent → hôte) :
```json
{"id":"req_001","type":"response","status":"success","payload":{}}
```

Événement temps réel (streaming, observabilité) :
```json
{"type":"event","event":"response.chunk","payload":{"text":"Bon..."}}
```

Erreur :
```json
{"type":"error","message":"Provider unavailable"}
```

Règle : `action` est toujours en minuscules avec un point (`runtime.configure`, `tool.result`).

## 3. Séquence obligatoire

1. `runtime.configure` — providers + clés + prompt système. À faire avant tout `chat.send`.
```json
{"type":"request","action":"runtime.configure","payload":{
  "system_prompt":"You are a helpful assistant.",
  "providers":[{"name":"groq","api_key":"gsk_xxx","model":"llama-3.3-70b-versatile"}]
}}
```
2. `host.manifest.register` — déclarer l'hôte (voir `core/host_manifest.py:18`, exemple `docs/host.manifest.example.json`).
3. `chat.send` — envoyer missions.
4. Écouter `response.chunk` / `response.completed` + `plan.generated` / `execution.completed`.

## 3b. Embeddings (lite / local / full)

L'hôte choisit le mode dans `runtime.configure` (simple pour l'utilisateur du setup) :
```json
{"type":"request","action":"runtime.configure","payload":{
  "system_prompt":"...",
  "providers":[...],
  "embeddings":{"mode":"lite"}
}}
```
- `"mode":"lite"` (défaut) : 0 Mo, offline, précision réduite. Suffit pour démarrer.
- `"mode":"local"` : `"model":"sentence-transformers/all-MiniLM-L6-v2"`. Précis, ~500 Mo + téléchargement au premier run dans `%APPDATA%/ManAgent/models`.
- `"mode":"remote"` : `"model":"text-embedding-3-small","api_key":"sk-...","base_url":"https://api.openai.com/v1"`. Précis, 0 Mo local, facturé à l'usage.
- Format avancé (legacy) : `"embedding_models":[{"type":"hash"|"sentence-transformer"|"remote",...}],"active_embedding_model":"..."`.

La réponse `runtime.configured` et l'événement associé renvoient `embeddings_mode` + `active_embedding_model`.
L'hôte peut déclarer ses capacités dans le manifest : `"capabilities":["mouse","keyboard","embeddings-local"]`.

## 3c. Gestion des modèles (UI setup — query, pas de dur)

Séquence recommandée pour l'écran modèles de l'hôte :
1. `{"action":"embeddings.catalog","payload":{}}` → `{"models":[{"id","display_name","type","languages","size_mb","dim","installed","size_bytes","active"}]}`. Remplir la liste + tailles + état installé. Jamais de modèle en dur côté hôte.
2. Bouton Télécharger → `{"action":"embeddings.prepare","payload":{"id":"..."}}` → events `embedding.download_started/finished/error` (+ progression `EMBEDDING_MODEL_LOADING/...` pour le local). Option `force:true` pour re-télécharger, `set_default:true` pour activer de suite.
3. Bouton Par défaut → `{"action":"embeddings.set_default","payload":{"id":"..."}}` → bascule à chaud (réponse + event `embedding.active_changed`). Erreur claire si non installé (« appelez embeddings.prepare d'abord »).
4. Alternative en une fois : `runtime.configure {"embeddings":{"mode":"lite|local|remote",...}}` (voir §3b).

## 4. Actions supportées (voir `core/constants.py:8`)

| Action | Usage |
|---|---|
| `runtime.configure` | config providers, obligatoire en premier |
| `host.manifest.register` | déclare tools + capabilities de l'hôte |
| `chat.send` | mission utilisateur |
| `chat.stop` / `chat.reset` | stop / reset conversation |
| `tool.result` | l'hôte renvoie le résultat d'un outil physique exécuté côté C++ |
| `skill.list_request` / `skill.payload_request` | lister / lire un skill |
| `skill.set_state` / `skill.repair` | quarantaine / réparation |
| `skill.export_package` / `skill.import_package` | partage de skills |
| `learner.analyze` | analyse post-mission |
| `system.warmup` / `system.reset_data` / `data.*` | maintenance |

## 5. Boucle outils (host exécute, ManAgent décide)

ManAgent ne clique jamais lui-même. Il demande (event `tool.requested`) :
`{"type":"event","event":"tool.requested","payload":{"call_id":"...","tool_name":"click","arguments":{...}}}`
L'hôte exécute côté C++ puis répond :
```json
{"type":"request","action":"tool.result","payload":{"call_id":"...","result":"..."}}
```
`call_id` = le ticket de suivi (obligatoire, tel quel). Mauvais ticket → erreur claire.

## 6. Observabilité (tout est observable)

Événements stables à logger côté hôte : `planner.start/finished`, `plan.generated`, `tools_manager.decision/execution/result`, `checkpoint.reached`, `breakout.occurred`, `execution.completed`, `learner.analyze_finished`. Voir `core/constants.py:31`, `transport/packet_models.py:39`.

## 7. Plan-first + rules

ManAgent répond toujours par un plan validé. L'utilisateur peut contraindre avec `rules.md` (confirmation humaine, rejets, niveaux de risque). L'hôte n'a pas besoin de relire les prompts.

## 8. Compatibilité (versioning)

ManAgent affiche sa version via `pyproject.toml` / `VERSION`. L'hôte envoie `host_version` dans le manifest. Règle : on n'ajoute que des champs optionnels, on ne renomme jamais une action existante sans montée de version majeure.

## 9. Checklist QProcess (Qt)

- `QProcess::start(MANAGENT_PYTHON, {MANAGENT_PATH})`, `setWorkingDirectory(managentDir)`
- `write(json + "\n")`, `readLine()` → `QJsonDocument::fromJson`
- Timeout `runtime.ready` : 15s. Timeout `chat.send` : selon mission, écouter `heartbeat`.
- Ne jamais parser stdout comme du texte, toujours comme JSON ligne par ligne.
- Règle v0 : ne traiter que les lignes qui commencent par `{`. Les lignes `[INFO]/[WARNING]`
  sont des logs mélangés sur stdout (limite connue, logs vers stderr prévu plus tard).
- Ne jamais aller lire les `.py`, utiliser ce contrat + `docs/protocol.md` + `docs/host.manifest.example.json`.

# PROTOCOL — Parler à ManAgent (v2, depuis le code réel)

> Document source de vérité, écrit depuis `core/orchestrator.py`.
> L'ancien `protocol.md` est périmé (voir `protocol.legacy.md`).
> Règle : une ligne = un JSON. Côté hôte, ne lire que les lignes qui commencent par `{`.

## 1. En 30 secondes

ManAgent est un programme à part (boîte noire). On le lance, on lui envoie des
demandes en JSON sur son entrée (stdin), il répond en JSON sur sa sortie (stdout).

```
Toi (hôte)                    ManAgent
   │                               │
   │── {"action":"runtime.configure",...} ──→ │  1. configurer (1 fois)
   │── {"action":"host.manifest.register",...} → │  2. se présenter (outils, OS)
   │── {"action":"chat.send",...} ──→        │  3. envoyer une mission
   │←── {"event":"response.chunk",...} ──    │  réponse petit à petit
   │←── {"response"...} ──                   │  réponse finale
```

## 2. Lancement

```
<python-ou-managent.exe> <chemin-vers-main.py-ou-rien>
```

- Premier message attendu : `{"type":"event","event":"runtime.ready","payload":{}}`
- Pas de `runtime.ready` en 15 s → tuer et relancer.
- Avec l'exe, le dossier de travail doit contenir `rules.md` (sinon règles vides).

## 3. Les 4 formes de messages

**Demande (toi → ManAgent) :**
```json
{"id": "req_001", "type": "request", "action": "chat.send", "payload": {"content": "Bonjour"}}
```
`id` = ton numéro de suivi (optionnel). `action` = ce que tu veux (liste §5).

**Réponse finale (ManAgent → toi) :**
```json
{"id": "req_001", "type": "response", "status": "success", "payload": {...}}
```

**Événement temps réel (progression, observabilité) :**
```json
{"type": "event", "event": "response.chunk", "payload": {...}}
```

**Erreur :**
```json
{"type": "error", "message": "..."}
```

## 4. Séquence obligatoire

1. `runtime.configure` — clés API + modèles (1 fois au démarrage).
2. `host.manifest.register` — qui es-tu : nom, version, OS, outils, capacités.
3. `chat.send` — les missions. Écouter les events entre les deux.

## 5. Toutes les actions (testées contre le code)

### Démarrage
| Action | À quoi ça sert | Exemple `payload` |
|---|---|---|
| `runtime.configure` | clés + modèles + embeddings | voir §6 |
| `host.manifest.register` | présenter l'hôte | `{"host_manifest": {...}}` (ou champs à plat) |
| `system.warmup` | préchauffer (embeddings, cache, prompts) en arrière-plan | `{}` |

### Conversation / mission
| Action | À quoi ça sert | Exemple `payload` |
|---|---|---|
| `chat.send` | envoyer un message / mission. `forced_provider` + `forced_model` OBLIGATOIRES (pas de choix auto pour l'instant) | `{"content": "Résume ce texte...", "session_id": "", "forced_provider": "gemini", "forced_model": "gemini-3.5-flash-lite"}` |
| `chat.stop` | arrêter d'urgence ce qui tourne | `{}` |
| `chat.reset` | tout arrêter + oublier la mission en cours | `{}` |
| `session.delete` | effacer une session (RAM + base) | `{"session_id": "..."}` |
| `tool.result` | renvoyer le résultat d'un outil exécuté côté hôte | `{"call_id": "...", "result": "..."}` (voir §7) |

### Modèles d'embedding (voir `docs/HOST_CONTRACT.md` §3b-3c)
| Action | À quoi ça sert | Exemple `payload` |
|---|---|---|
| `embeddings.catalog` | liste des modèles + installés ou pas | `{}` |
| `embeddings.prepare` | télécharger/précharger un modèle (avec progression) | `{"id": "sentence-transformers/all-MiniLM-L6-v2", "force": false, "set_default": false}` |
| `embeddings.set_default` | changer de modèle à chaud | `{"id": "lite-hash"}` |

### Compétences (skills : brouillon → test → production → quarantaine)
| Action | À quoi ça sert | Exemple `payload` |
|---|---|---|
| `skill.list_request` | lister les skills | `{}` |
| `skill.payload_request` | lire le contenu d'un skill | `{"skill_id": "...", "version": 1}` |
| `skill.set_state` | changer d'état | `{"skill_id": "...", "state": "QUARANTINE", "version": 1}` (états : DRAFT, SHADOW, PRODUCTION, QUARANTINE) |
| `skill.repair` | réparer avec un LLM (en arrière-plan) | `{"skill_id": "...", "version": 1}` |
| `skill.export_package` | exporter pour partager | `{"skill_id": "..."}` |
| `skill.import_package` | importer un package | `{"package": {...}}` |

### Mémoire et maintenance
| Action | À quoi ça sert | Exemple `payload` |
|---|---|---|
| `learner.analyze` | ré-analyser les missions passées (leçons) | `{"force": false}` |
| `data.stats` | compter (épisodes, leçons, sessions, skills...) | `{}` |
| `data.export` | tout exporter (épisodes, leçons, skills) | `{}` |
| `data.purge` / `system.reset_data` | effacer. `target` : all, cache, episodes, lessons, skills, sessions... | `{"target": "all"}` (défaut : tout) |

Action inconnue → `{"type":"error","message":"Unknown action: ..."}`. Jamais de crash.

## 6. `runtime.configure` en détail (format réel)

```json
{
  "system_prompt": "You are a helpful assistant.",
  "language": "fr",
  "protocol_version": "1",
  "environment": "simulated",
  "hitl_policy": "balanced",
  "api_keys": {"gemini": "TA_CLÉ"},
  "models_registry": {"providers": {"gemini": {"models": [
    {"id": "gemini-3.5-flash-lite", "display_name": "Gemini 3.5 Flash-Lite", "capabilities": ["text"]}
  ]}}},
  "host_manifest": {...},
  "tools": [...],
  "embeddings": {"mode": "lite"},
  "embedding_catalog_extra": [...]
}
```
- `hitl_policy` : strict (demande souvent), balanced, autonomous (demande rarement).
- `protocol_version` : version du langage ("1"). Renvoyée dans `runtime.configured`.
  Absente = accepté + avertissement (transition). Différente = erreur bruyante
  `protocol version mismatch`, pas de session. À monter à chaque changement incompatible.
- `capabilities` d'un modèle : `text`, `vision`, `tools`... (sert au routage).
- Réponse : `{"models_count": 1, "embeddings_mode": "lite", ...}`. Si `0`, le format des modèles est faux.

## 7. Boucle outils (l'hôte exécute, ManAgent décide)

ManAgent ne clique jamais lui-même. Quand il a besoin d'un outil hôte :
```
← {"event":"tool.requested","payload":{"call_id":"abc123","tool_name":"click","arguments":{...}}}
```
L'hôte exécute (vrai clic, vraie touche...) puis répond :
```json
{"action": "tool.result", "payload": {"call_id": "abc123", "result": "..."}}
```
`call_id` = le ticket de suivi. Mauvais ticket → erreur claire, pas de crash.

## 8. Événements à connaître (observabilité)

Santé : `runtime.ready`, `runtime.configured`, `runtime.error`, `heartbeat`.
Mission : `mission.started`, `plan.generated`, `step.status_changed`, `plan.abandoned`, `mission.failed`.
Streaming : `thinking.started`, `response.chunk`, `response.completed`, `thinking.finished`.
Outils : `tool.requested`, `executor.run_tool`, `tools_manager.decision/execution/result/error`.
Skills : `checkpoint.reached`, `breakout.occurred`, `execution.completed`, `skill.state_changed`.
Embeddings : `embedding.download_started/finished/error`, `embedding.active_changed`, `host.manifest_updated`.
Apprentissage : `learner.analyze_started/finished`, `discovery.session_start/step/session_end`.

## 9. Règles du jeu

1. Une ligne = un JSON. Le reste (logs `[INFO]`) s'ignore.
2. Toujours configurer avant de discuter.
3. On n'enlève jamais une action existante sans changer de version majeure.
4. `rules.md` (côté ManAgent) contraint les plans : confirmations humaines, rejets, risques.

# HOST_CONTRACT — Comment utiliser ManAgent sans lire son code

ManAgent est une boîte noire (black box = on l'utilise sans voir l'intérieur).
L'hôte (Qt, Web, CLI) ne touche JAMAIS aux `.py`. Il lance un processus, envoie du JSON sur stdin (entrée standard), lit du JSON sur stdout (sortie standard).

Référence complète : `docs/PROTOCOL.md`. Ce contrat est le résumé stable pour intégrateur.

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
- Handshake de version : `runtime.configure` envoie `"protocol_version": "1"` (top-level) ; `runtime.configured` la renvoie. Mismatch = erreur bruyante, pas de session.

Santé (healthcheck) : si pas de `runtime.ready` en 15s → tuer le QProcess et relancer.

## 2. Paquets (packets)

Requête (hôte → ManAgent), voir `transport/packet_models.py:17` :
```json
{"id":"req_001","type":"request","action":"chat.send","payload":{"content":"Bonjour"}}
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
2. `host.manifest.register` — déclarer l'hôte (exemple `docs/host.manifest.example.json`).
   Chaque outil déclare `kind` : `"perception"` (lit le monde sans le changer)
   ou `"action"` (le change). Sans `kind` = traité comme action (prudence).
   Vocabulaire ouvert : un robot déclare pareil (`lidar_scan` = perception...).
3. `chat.send` — envoyer missions.
4. Écouter `response.chunk` / `response.completed` + `plan.generated` / `execution.completed`.

## 3a. Lire le monde (méta-outil `perceive_understand`)

Règle : le planner n'appelle JAMAIS un outil `[perception]` en direct.
Pour voir avant de décider ou vérifier après avoir agi, il utilise :
```json
{"tool_name": "perceive_understand", "question": "où est la porte ?",
 "source_tool": "lidar_scan", "source_args": {},
 "format_response": "direction et distance en mètres"}
```
- `question` : que chercher, en langage naturel (obligatoire).
- `source_tool` + `source_args` : quel outil hôte appeler (ou `source_data` : variable déjà disponible).
- `format_response` : format strict attendu (vide = rapport libre).
Le système appelle la source, fait comprendre par LLM, renvoie la valeur.

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
3. Bouton Par défaut → `{"action":"embeddings.set_default","payload":{"id":"..."}}` → bascule à chaud (réponse + event `embedding.active_changed`). Erreur claire si non installé (« appelez embeddings.prepare d'abord »). Mémoire séparée par modèle : pas de mélange, anciennes données préservées.
4. Bouton Annuler (pendant un download) → `{"action":"embeddings.cancel","payload":{"id":"..."}}` (reprise auto au prochain prepare). Le prepare vérifie l'espace disque avant et refuse clairement si insuffisant.
4. Alternative en une fois : `runtime.configure {"embeddings":{"mode":"lite|local|remote",...}}` (voir §3b).

## 3d. Nom du monde (alias, optionnel)

La découverte d'état (`world`) affiche une cible fictive stable car le monde
n'a pas d'inventaire : `"this_world"` par défaut. Pour un nom métier
(ex : `"atelier"`, `"ligne-3"`), déclarez `"metadata": {"world_alias": "atelier"}`
dans le manifeste. Pas de nouveau champ : `metadata` est libre.

## 3e. Descriptions d'outils rigides (obligatoire, imposé par ManAgent)

Le cerveau ne voit que vos descriptions. Flou = hallucination mécanique,
pas mauvaise volonté. Règles absolues pour chaque outil déclaré :

1. **Provenance des refs** : tout param de type référence (IDs, fenêtres,
   cibles, cases) doit dire d'où vient la valeur (sortie d'un outil
   précédent, verbatim, jamais inventée) et nommer l'erreur de refus sinon.
   Ex : `target_id` vient d'une sortie `perceive` de cette mission, inventé
   = échec `STALE_REF`.
2. **Enchaînements** : si un outil en nécessite un autre avant lui
   (percevoir avant d'attendre, capturer avant de cliquer), écrivez-le.
3. **Danger explicite** : chaque interdit porte sa sanction
   (`Ne devine jamais : sans ID valide, échec TARGET_NOT_FOUND`).
   Une permission sans garde (`pas besoin de l'avoir perçu`) sera lue
   comme une autorisation d'inventer.
4. **Exemple d'usage si ambigu** : si un paramètre se comprend mal
   (format d'ID, guillemets, casse), ajoutez un exemple d'appel complet
   dans la description.
5. **Garde-fous d'adaptation** : vos outils doivent tolérer les données
   du cerveau (espaces, guillemets parasites, casse) et adapter en
   silence quand c'est sans risque, ou refuser avec le motif exact sinon.
   Le cerveau suit les descriptions au mieux ; si rien n'est clair, il
   devine — c'est mécanique.

## 3f. Effets des outils (optionnel)

Chaque outil peut déclarer `effects` : `"deterministic"` (le résultat
annoncé égale toujours l'effet réel) ou `"uncertain"` (les deux peuvent
différer). Défauts prudents : `[action]` = `"uncertain"`, `[perception]`
et utilitaires = rien. Un hôte déterministe déclare `"deterministic"`
une fois et ne paie aucune vérification en plus. Détail : `docs/ALIGNMENT.md`.
- `effects_timeout_ms` (optionnel, entier, millisecondes) : attente
  insérée par le cerveau après chaque appel réussi à cet outil. Absent
  = aucune attente.
- `changing_effects` (optionnel, liste ouverte) : events de changement
  que l'hôte sait détecter (`foreground_changed`, `focus_changed`,
  `screen_diff`, `app_loading`, `app_closing`…) + `max_wait_ms`
  par outil (défaut hôte sinon). L'hôte attend activement après une
  action marquée, retourne `false + no_world_change_detected` sinon.
  Le cerveau ne bloque jamais : timeout client = `max_wait_ms` + marge.

## 3g. État du monde (optionnel, recommandé pour les hôtes visuels)

Pour que le cerveau sache « où il est » sans deviner :
- Soit déclarez `world_snapshot: {source_tool, source_args, max_chars}` (metadata ou environment du manifeste) : source lue à la demande, texte capé.
- Soit exposez un outil nommé `get_world_state`, kind `[perception]`, sans param obligatoire : il sera appelé par défaut (texte court : fenêtre avant-plan, focus, état).
- Sinon rien : le cerveau continue sans photo (nécessite un redémarrage du raisonnement sans contexte).
- Points de lecture : avant faisabilité, avant chaque plan, avant chaque convergence (jamais jugé comme preuve, que du contexte).
- Convention `get_world_state` documentée ici : retour texte brut ou `{summary_text, full}` (résumé dans le prompt, détail via PD).

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
| `stats.get` | compteurs tokens : `{"mission_id":"..."}` → usage, ou sans filtre → total + par mission |
| `rules.get` / `rules.set` | lire / changer les règles à chaud : `{"rules_text":"..."}` |

## 5. Boucle outils (l'hôte exécute, ManAgent décide)

ManAgent ne fait jamais le travail lui-même. Il demande (event `tool.requested`) :
`{"type":"event","event":"tool.requested","payload":{"call_id":"...","tool_name":"...","arguments":{...}}}`
L'hôte fait le VRAI travail (pas du faux), puis répond TOUJOURS avec ce format :
```json
{"type":"request","action":"tool.result","payload":{"call_id":"...","result":"..."}}
```
Le champ `result` est lui-même du JSON en texte, avec 3 clés simples :
- `result` : `true` (ça a marché) ou `false` (raté). Comparé à ce que le plan attendait.
- `data` : le résultat utile (optionnel : texte, objet, liste, ce que tu veux).
- `error_reason` : pourquoi ça a raté, avec les mots (si raté). Long texte accepté.

Exemple succès — l'outil a additionné :
```json
{"call_id":"abc123","result":"{\"result\": true, \"data\": {\"total\": 42}}"}
```
Exemple échec honnête — l'élément n'existe pas :
```json
{"call_id":"abc123","result":"{\"result\": false, \"error_reason\": \"bouton introuvable à l'écran\"}"}
```
`call_id` = le ticket de suivi (obligatoire, tel quel). Mauvais ticket → erreur claire.
Grosse donnée (> ~3000 caractères) = rangée à part et paginée, pas de panique.

## 5a. Sorties typées (optionnel, imposé par ManAgent)

Par défaut, `data` est traité comme du texte. Si un outil renvoie une
charge non textuelle (image, audio, PDF...), déclarez-la dans le manifeste,
par outil, champ `returns` (liste). Vocabulaire imposé : types MIME
standard (`image/jpeg`, `image/png`, `application/pdf`...), jamais de
noms propres. Vos noms d'outils et de champs restent libres.
```json
{"name": "capturer_image", "kind": "perception",
 "parameters": {"type": "object", "properties": {}, "required": []},
 "returns": [
   {"field": "image_base64", "asset": "image/jpeg",
    "description": "Photo d'écran encodée base64."},
   {"field": "cadre", "asset": "application/json",
    "description": "Structure détectée."}
 ]}
```
- `field` : chemin pointé dans `data` (ex : `image_base64`, ou `apercu.vignette`).
- `asset` : type imposé. `image` seul suffit : ManAgent vérifie les octets
  (signature magique) et étiquette le vrai type. Un MIME plein (`image/jpeg`...)
  reste conseillé mais le contenu fait foi. Seuls les non-texte (`image`, `image/*`,
  `video/*`, `audio/*`, `application/pdf`) sont extraits en assets. Le reste reste inline.
- ManAgent enregistre chaque charge comme asset typé et expose son adresse
  (`outputs://...`) dans le registre. L'analyse d'image utilise l'adresse,
  jamais le base64. Sans déclaration `returns`, comportement inchangé (texte).

Types gérés, de bout en bout (déclaration → modèle) :

| Famille | Exemples | Sort réel |
| `image/*` | jpeg, png, webp, gif, bmp | pixels au modèle, si le modèle a la vision, sinon refus clair |
| `application/pdf` | pdf | document au modèle |
| `video/*`, `audio/*` | mp4, mp3 | enregistrés comme assets, pas encore envoyés au modèle |
| texte, JSON, CSV | le reste | inline + forage par tranches |

## 6. Observabilité (tout est observable)

Événements stables à logger côté hôte : `planner.start/finished`, `plan.generated`, `tools_manager.decision/execution/result`, `checkpoint.reached`, `breakout.occurred`, `execution.completed`, `learner.analyze_finished`. Voir `core/constants.py:31`, `transport/packet_models.py:39`.

Inspection des prompts (debug) : lancez avec `MANAGENT_RECORD_PROMPTS=1` —
chaque prompt rendu est copié dans `prompts_log/` (dossier de données) avec
la liste des variables (noms seuls, jamais les valeurs/secrets).

Monde vivant via discovery : les entités autorisées (solver, planner en
retry, convergence) peuvent demander `world` (`inspect_state`,
`locate_target`, `verify_effect`) quand les données en main ne suffisent pas
— jamais systématiquement. Préavis hôte : chaque appel arrive en
`tool.requested` comme d'habitude, rien de nouveau à câbler.

## 7. Plan-first + rules

ManAgent répond toujours par un plan validé. L'utilisateur peut contraindre avec `rules.md` (confirmation humaine, rejets, niveaux de risque). L'hôte n'a pas besoin de relire les prompts.

Dev (fichier) : éditez `rules.md` + relancez. Prod (exe, sans fichier) : envoyez `runtime.configure {"rules_text":"..."}` une fois, ou `rules.set` à chaud + `rules.get` pour relire. L'override en mémoire est prioritaire sur le fichier. Vide = refusé avec erreur claire.

## 7b. Compteurs tokens (usage)

Chaque `llm_call` porte `usage = {prompt_tokens, completion_tokens, total_tokens, source}` où `source` vaut `real` (chiffre fournisseur), `estimated` (calcul local ~4 caractères = 1 token) ou absent. Jamais inventé : si le fournisseur ne dit rien, on estime et on l'écrit.

Query hôte :
```json
{"type":"request","action":"stats.get","payload":{"mission_id":"m123"}}
→ {"mission_id":"m123","found":true,"prompt_tokens":1200,"completion_tokens":800,"total_tokens":2000,"calls":5,"real_calls":4,"estimated_calls":1}
{"type":"request","action":"stats.get","payload":{}}
→ {"total":{...},"by_mission":{...}}
```
Mission inconnue → `found:false` + zéros, pas d'erreur.

## 7c. Automatismes (skills) : qui décide des chiffres

Par défaut, ManAgent crée un automatisme après **2 succès de suite**, le teste 1 fois en fantôme, le met en quarantaine après **3 échecs**, le répare max **3 fois**. Ces chiffres sont des défauts affichés ici — c'est toi qui décides pour ton contexte.

| Réglage | Défaut | Sens simple |
|---|---|---|
| `discovery_threshold` | 2 | succès de suite avant de créer l'automatisme |
| `shadow_success_threshold` | 1 | essais fantôme réussis avant mise en prod |
| `shadow_mismatch_threshold` | 3 | désaccords avant quarantaine |
| `circuit_breaker_max_failures` | 3 | échecs de suite avant quarantaine |
| `max_repairs` | 3 | réparations avant retraite (`null` = infini, assumé) |
| `champion_margin` | 0.05 | avance exigée pour remplacer la version en prod |

3 façons de régler, de la plus large à la plus précise (la plus précise gagne, et chaque décision dit sa source dans les events) :
1. **Global** : `"skill_governance": {"circuit_breaker_max_failures": 1}` dans le manifeste.
2. **Sensibilité des outils** : `"sensitivity": "high"` sur un outil (ex : virement bancaire) → l'automatisme devient strict tout seul (seuils divisés par 2). `"low"` → souple (×2). Sans rien = standard.
3. **Par automatisme** : `"skill_governance": {"skills": {"skill.virement.envoyer": {"circuit_breaker_max_failures": 1}}}` (l'ID se lit dans `skill.list_request`).

Banque (strict, 1 échec suffit) :
```json
{"host_name": "banque", "tools": [{"name": "virement.envoyer", "sensitivity": "high", "requires_env": ["session_auth"]}], "skill_governance": {"max_repairs": 1}}
```
Démo (souple, on expérimente) :
```json
{"host_name": "demo", "skill_governance": {"circuit_breaker_max_failures": 5, "max_repairs": null}}
```
Règle d'or : un automatisme importé d'ailleurs démarre en fantôme (jamais confiance immédiate). Une v2 ne remplace la v1 qu'avec plus de preuves (marge ci-dessus).

## 7d. Exploration (limites réglables)

Quand le cerveau manque d'info, il explore (max 5 allers-retours et 10 étapes par défaut). Si le plafond est touché, l'event `discovery.ceiling_hit` le dit. Pour explorer plus :
```json
{"type":"request","action":"runtime.configure","payload":{"discovery":{"max_iterations":8,"max_session_steps":12}}}
```
Borné 1..20, défauts sinon. Plafond absolu : 20 (anti-boucle infinie).

## 7e. Embeddings : qui utilise ton modèle, combien ça pèse

Ton modèle choisi (`embeddings.mode` + catalogue) sert PARTOUT : mémoire des missions, leçons (écriture ET lecture), profils skills. Un seul espace à la fois, jamais de mélange. Leçons d'un autre modèle = ignorées avec log (garde dimension).

Poids mémoire vive (RAM) approximatifs :
| Modèle | RAM |
|---|---|
| `lite` (défaut) | 0 Mo, offline |
| MiniLM L6 / multilingue / e5-small | ~500 Mo |
| BGE-M3 | ~2,3 Go |
| `remote` (API) | 0 Mo local, facturé à l'usage |

1 Go+ en dev avec BGE-M3 = normal (torch + poids). Pour alléger : `lite` ou `remote`.

## 8. Compatibilité (versioning)

ManAgent affiche sa version via `pyproject.toml` / `VERSION`. L'hôte envoie `host_version` dans le manifest. Règle : on n'ajoute que des champs optionnels, on ne renomme jamais une action existante sans montée de version majeure.

## 9. Checklist QProcess (Qt)

- `QProcess::start(MANAGENT_PYTHON, {MANAGENT_PATH})`, `setWorkingDirectory(managentDir)`
- `write(json + "\n")`, `readLine()` → `QJsonDocument::fromJson`
- Timeout `runtime.ready` : 15s. Timeout `chat.send` : selon mission, écouter `heartbeat`.
- Ne jamais parser stdout comme du texte, toujours comme JSON ligne par ligne.
- Règle v0 : ne traiter que les lignes qui commencent par `{` (sécurité).
  Les logs vont déjà sur stderr côté ManAgent (vérifié : seul le JSON va sur
  stdout) ; le filtre reste en ceinture + bretelles.
- Ne jamais aller lire les `.py`, utiliser ce contrat + `docs/protocol.md` + `docs/host.manifest.example.json`.

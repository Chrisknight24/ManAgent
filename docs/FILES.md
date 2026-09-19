# FILES — Où est quoi dans ManAgent (pour s'y retrouver vite)

> L'hôte n'a besoin d'AUCUN de ces fichiers (voir `HOST_CONTRACT.md`).
> Cette page sert aux mainteneurs de ManAgent.

## Entrée
- `main.py` — démarre tout : transport + orchestrateur, boucle de lecture JSON.
- `managent.spec`, `tools/build_exe.py` — recette + script de cuisson de l'exe.
- `pyproject.toml`, `requirements.txt` — dépendances. `pytest.ini` — réglage tests.

## Cerveau (core/)
- `core/orchestrator.py` — le chef : reçoit les actions (§5 du protocole), coordonne.
- `core/planner.py` — écrit les plans. `core/plan_validator.py` — les juge (avec `rules.md`).
- `core/executor.py`, `core/solver.py` — exécutent les étapes, appellent les outils.
- `core/constants.py` — la liste officielle des actions + événements (source du protocole).
- `core/runtime_state.py` — la mémoire de travail (config, manifest hôte, embeddings actifs).
- `core/llm.py` — parle aux LLM via le gestionnaire de providers.
- `core/learner.py`, `core/entity_learner.py` — apprennent des missions (leçons).
- `core/mission_compactor.py`, `core/retriever.py`, `core/cache.py` — résument, retrouvent, cachent.
- `core/prompt_loader.py` — charge `prompts/` (3 langues : base, fr, en).
- `core/host_manifest.py` — carte d'identité de l'hôte (outils, capacités, OS).
- `core/event_bus.py` → `core/event_forwarder.py` — tuyau interne vers la sortie JSON.
- `core/skills/` — cycle de vie des compétences (brouillon, test, production, quarantaine).
- `core/discovery/` — enquête progressive avant d'agir (fichiers, historique, registre...).
- `core/markers/`, `core/supervisor.py`, `core/presentator.py` — suivi, arbitrage, présentation.

## Bords (plomberie)
- `transport/` — entrée/sortie JSON (`stdin_transport.py`, `packet_models.py`). Seul point à changer pour WebSocket/HTTP plus tard.
- `providers/` — un fichier par fournisseur LLM (openai, gemini, groq, anthropic, deepseek, openrouter) + `provider_manager.py` (routage, clés multiples).
- `embeddings/` — `base.py` (contrat), `manager.py` (aiguillage), `catalog.py` (liste + détection), `providers/` (hash lite, distant API, local lourd).
- `memory/` — bases SQLite : missions, leçons, sessions, profils (vectoriel via `vec0`).
- `tools/` — outils internes (`tools_manager.py`, `internal_tools.py`), labos, `build_exe.py`, rapport d'observabilité.
- `utils/` — logs, config, i18n (traductions `locale/` + `core/i18n.py`).

## Config et docs (à côté du code, modifiables sans recompiler)
- `rules.md` — règles des plans (confirmations, rejets). `memory.db` — base locale (jamais sur git).
- `docs/` — `PROTOCOL.md` (le vrai protocole), `HOST_CONTRACT.md` (guide hôte), `BUILD_EXE.md` (cuisson), `host.manifest.example.json`, `FILES.md` (cette page).

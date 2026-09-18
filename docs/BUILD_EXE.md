# BUILD_EXE — Cuisiner managent.exe v0 (Windows)

But : un seul fichier `managent.exe`, boîte noire. L'app Qt le lance via QProcess, aucun `.py` visible.

## 0. Principe (à retenir)
- `PyInstaller` = outil qui emballe ton Python + ton code + tes prompts dans un `.exe`.
- Dedans l'exe : code + prompts par défaut (lecture seule).
- Dehors l'exe : `rules.md`, `memory.db`, `host.manifest.json`, clés API (variables d'environnement).
- v0 = version **lite** (sans torch) : légère (~30-60 Mo), offline, embeddings hash. La full viendra après.

## 1. Préparer (une fois)
```powershell
cd C:\... \ManAgent
.venv\Scripts\activate
pip install pyinstaller
pip install -r requirements.txt
```

## 2. Fichiers à créer (mission suivante, je les prépare)
- `managent.spec` : recette PyInstaller (quels dossiers embarquer : `core/`, `providers/`, `prompts/`, `transport/`, `utils/`, `memory/` sans `memory.db`).
- `tools/build_exe.py` : script qui nettoie, build, et copie l'exe vers `dist/`.

## 3. Cuisson v0 (toi, 1 commande)
```powershell
.venv\Scripts\activate
pyinstaller managent.spec --noconfirm
dir dist\managent\managent.exe
```

## 4. Test v0 (toi, sans Qt d'abord)
```powershell
# 4a. Version
dist\managent\managent.exe --version
# 4b. Santé : doit afficher {"type":"event","event":"runtime.ready",...}
echo {"type":"request","action":"system.warmup","payload":{}} | dist\managent\managent.exe
# 4c. Mission simple via fichier
echo {"type":"request","action":"runtime.configure","payload":{"providers":[]}} | dist\managent\managent.exe
```

## 5. Brancher Qt (l'autre agent)
`QProcess::start("C:/.../managent.exe")`, dossier de travail = dossier contenant `rules.md` + `memory.db`.
Voir `docs/HOST_CONTRACT.md` : le contrat JSON ne change pas entre `.py` et `.exe`.

## 6. Lite vs Full (choix user)
- Setup hôte propose : `[] Lite (recommandé, léger)` / `[] Full (recherche sémantique, ~500 Mo, télécharge le modèle au premier run dans %APPDATA%/ManAgent/models)`.
- Switch après install : `runtime.configure {"embeddings": {"mode": "lite"}}` ou `{"mode": "local", "model": "..."}` ou `{"mode": "remote", ...}`.

## 7. Pièges connus
- Antivirus : un exe PyInstaller neuf peut alerter. Solution : signer plus tard (certificat), ou ajouter une exception locale en dev.
- `--onefile` (un seul fichier) démarre plus lentement (décompression à chaque run). v0 : `--onedir` (dossier), plus rapide pour QProcess qui relance souvent.
- Ne jamais mettre les clés API dans l'exe. Variables d'environnement côté hôte.

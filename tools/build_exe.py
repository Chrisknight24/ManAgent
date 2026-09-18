"""tools/build_exe.py
====================
Script de cuisson locale de managent.exe (toi qui cuisines, sur Windows).

Usage :
    .venv\\Scripts\\activate
    python tools\\build_exe.py --mode lite
    python tools\\build_exe.py --mode full

Verifie : spec present, pyinstaller installe, dossier dist/ produit.
Ne televerse rien, ne signe rien (etapes suivantes).
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, env=None):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, env=env or os.environ)
    if r.returncode != 0:
        raise SystemExit(f"Echec ({r.returncode}) : {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lite", "full"], default="lite")
    args = ap.parse_args()

    spec = os.path.join(ROOT, "managent.spec")
    if not os.path.isfile(spec):
        raise SystemExit("managent.spec introuvable a la racine.")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller absent : pip install pyinstaller")

    env = dict(os.environ)
    env["MANAGENT_BUILD"] = args.mode

    # Nettoyage du build precedent (dossier ephemere)
    for d in ("build",):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            print(f"Nettoyage {d}/", flush=True)
            shutil.rmtree(p, ignore_errors=True)

    run([sys.executable, "-m", "PyInstaller", "managent.spec", "--noconfirm"], env=env)

    exe = os.path.join(ROOT, "dist", "managent", "managent.exe")
    if not os.path.isfile(exe):
        raise SystemExit("Build termine mais managent.exe introuvable dans dist/managent/.")

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"OK : {exe} ({size_mb:.1f} Mo, mode={args.mode})", flush=True)

    # Fichiers externes conseilles a cote de l'exe (config modifiable)
    print("Rappel : a cote de l'exe, prevoir rules.md, host.manifest.json, memory.db (cree au run).", flush=True)

    # vec0.dll optionnel (sqlite-vec) : copie si present a la racine
    dll = os.path.join(ROOT, "vec0.dll")
    if os.path.isfile(dll):
        shutil.copy2(dll, os.path.join(ROOT, "dist", "managent", "vec0.dll"))
        print("vec0.dll copie (optionnel, recherche vectorielle).", flush=True)
    else:
        print("vec0.dll absent : recherche texte simple (pas de vectoriel).", flush=True)


if __name__ == "__main__":
    main()

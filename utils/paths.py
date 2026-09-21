"""
utils/paths.py
==============
Dossier de donnees (data dir) + version, pour l'exe deploye.

Principe : le code et les prompts sont embarques (lecture seule),
tout ce qui S'ECRIT va dans le data dir :
  memory.db, rules.md (copie modifiable), observability/events.jsonl

Ordre : --data-dir (argument) > MANAGENT_DATA_DIR (variable) > dossier courant.
"""
import os
import re
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Vrai quand on tourne dans l'exe PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    """Dossier contenant managent.exe (fige) ou main.py (source)."""
    if is_frozen():
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def bundle_dir() -> Path:
    """Dossier des ressources embarquees (_internal en fige, racine en source)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return exe_dir()


def get_version() -> str:
    """Version depuis pyproject.toml (embarque ou source), 'unknown' sinon."""
    for base in (bundle_dir(), exe_dir()):
        pp = base / "pyproject.toml"
        if not pp.is_file():
            continue
        try:
            text = pp.read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.M)
            if m:
                return m.group(1)
        except OSError:
            continue
    return "unknown"


def resolve_data_dir(cli_value=None) -> Path:
    """Calcule le data dir effectif (sans le creer)."""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ.get("MANAGENT_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def setup_data_dir(cli_value=None) -> Path:
    """Cree le data dir, y copie rules.md par defaut si absent, s'y place.

    Retourne le data dir. A appeler UNE fois au demarrage (main.py).
    """
    data_dir = resolve_data_dir(cli_value)
    data_dir.mkdir(parents=True, exist_ok=True)

    # rules.md : graine modifiable pour l'utilisateur/hote.
    dest = data_dir / "rules.md"
    if not dest.is_file():
        for src in (bundle_dir() / "rules.md", exe_dir() / "rules.md"):
            if src.is_file():
                try:
                    shutil.copy2(src, dest)
                    break
                except OSError:
                    continue

    os.chdir(data_dir)
    return data_dir

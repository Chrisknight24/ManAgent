"""
embeddings/catalog.py
=====================
Catalogue de modeles d'embedding utilisables par ManAgent.

Agnostique : simple liste curatee + extensible. L'hote ajoute ses entrees
via runtime.configure {"embedding_catalog_extra": [...]}. Rien n'est fige
sur les modeles installes chez un dev : la presence disque est DETECTEE
(scan du cache), jamais supposee.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Taille indicative (Mo) pour afficher avant telechargement. Approximatif.
CATALOG: List[Dict[str, Any]] = [
    {
        "id": "lite-hash",
        "display_name": "Lite Hash (offline, instantane)",
        "type": "hash",
        "languages": ["all"],
        "size_mb": 0,
        "dim": 256,
        "description": "Repli local sans modele, zero telechargement.",
    },
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "display_name": "MiniLM L6 (anglais, leger)",
        "type": "sentence-transformer",
        "languages": ["en"],
        "size_mb": 90,
        "dim": 384,
        "description": "Bon compromis vitesse/qualite pour l'anglais.",
    },
    {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "display_name": "MiniLM multilingue (dont francais)",
        "type": "sentence-transformer",
        "languages": ["multilingual"],
        "size_mb": 470,
        "dim": 384,
        "description": "Multilingue dont le francais, ~470 Mo.",
    },
    {
        "id": "intfloat/multilingual-e5-small",
        "display_name": "E5 small multilingue",
        "type": "sentence-transformer",
        "languages": ["multilingual"],
        "size_mb": 470,
        "dim": 384,
        "description": "Multilingue compact et precis.",
    },
    {
        "id": "BAAI/bge-m3",
        "display_name": "BGE-M3 multilingue (lourd)",
        "type": "sentence-transformer",
        "languages": ["multilingual"],
        "size_mb": 2300,
        "dim": 1024,
        "description": "Tres precis, ~2,3 Go. A proposer avec avertissement.",
    },
    {
        "id": "remote:text-embedding-3-small",
        "display_name": "OpenAI text-embedding-3-small (API)",
        "type": "remote",
        "languages": ["multilingual"],
        "size_mb": 0,
        "dim": 1536,
        "description": "Via API, 0 Mo local, facture a l'usage.",
    },
]


def get_catalog(extra: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Catalogue + entrees supplementaires de l'hote (sans doublons, l'hote gagne)."""
    merged: Dict[str, Dict[str, Any]] = {e["id"]: dict(e) for e in CATALOG}
    for e in extra or []:
        if isinstance(e, dict) and e.get("id"):
            merged[e["id"]] = dict(e)
    return list(merged.values())


def default_cache_dir() -> Path:
    """Dossier cache HuggingFace, multi-OS, sans chemin en dur."""
    env = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if env:
        return Path(env) / "hub" if Path(env).name != "hub" else Path(env)
    return Path.home() / ".cache" / "huggingface" / "hub"


def cache_subdir(model_id: str) -> str:
    """Nom du dossier cache pour un id 'org/nom' (convention HuggingFace)."""
    return "models--" + model_id.replace("/", "--")


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def detect_installed(
    model_ids: List[str], cache_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """Detecte la presence disque de modeles (vrai telechargement complet ou non).

    Retour : {id: {"installed": bool, "size_bytes": int}}.
    Fonction pure et testable (cache_dir injectable).
    """
    base = Path(cache_dir) if cache_dir else default_cache_dir()
    out: Dict[str, Dict[str, Any]] = {}
    for mid in model_ids:
        if mid in ("lite-hash", "hash", "lite") or mid.startswith("remote:"):
            out[mid] = {"installed": True, "size_bytes": 0}
            continue
        d = base / cache_subdir(mid)
        snap = d / "snapshots"
        installed = False
        size = 0
        if snap.is_dir():
            # Complet si au moins un snapshot contient des poids.
            for rev in snap.iterdir():
                if not rev.is_dir():
                    continue
                weights = (
                    list(rev.glob("*.safetensors"))
                    + list(rev.glob("*.bin"))
                    + list(rev.glob("*.onnx"))
                )
                if weights:
                    installed = True
                    break
            size = _dir_size_bytes(d)
        elif d.is_dir():
            size = _dir_size_bytes(d)
        out[mid] = {"installed": installed, "size_bytes": size}
    return out


def catalog_status(
    extra: Optional[List[Dict[str, Any]]] = None,
    cache_dir: Optional[Path] = None,
    active_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Catalogue enrichi : statut d'installation + actif. Pret pour l'UI hote."""
    entries = get_catalog(extra)
    states = detect_installed([e["id"] for e in entries], cache_dir)
    rows = []
    for e in entries:
        st = states.get(e["id"], {"installed": False, "size_bytes": 0})
        rows.append({**e, **st, "active": e["id"] == active_id})
    return rows

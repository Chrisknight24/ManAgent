# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec pour managent.exe (boite noire).
Usage (toi, sur Windows) :
    .venv\\Scripts\\activate
    set MANAGENT_BUILD=lite & pyinstaller managent.spec --noconfirm
    set MANAGENT_BUILD=full & pyinstaller managent.spec --noconfirm
- lite (defaut) : sans torch/transformers -> exe leger, embeddings hash.
- full : avec modeles locaux (lourd, ~500 Mo+).
"""
import os

BUILD = os.environ.get("MANAGENT_BUILD", "lite").lower().strip()

_base_excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "notebook",
    "pytest", "_pytest", "asyncio.test",
]
if BUILD != "full":
    _base_excludes += [
        "torch", "transformers", "sentence_transformers",
        "sklearn", "scipy",
    ]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("prompts", "prompts"),
        ("rules.md", "."),
        ("locale", "locale"),
        ("pyproject.toml", "."),
    ],
    hiddenimports=[
        "pydantic", "jinja2", "aiohttp", "httpx",
        "numpy", "google.genai",
        "embeddings.base", "embeddings.manager",
        "embeddings.providers.hash_provider",
        "embeddings.providers.remote_provider",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_base_excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="managent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # stdin/stdout JSON : console obligatoire
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="managent",
)

# -*- coding: utf-8 -*-
"""
core/skills/flo_adapter.py
Module de compatibilité ascendante redirigeant vers le moteur de flux canonique ManAgent (canonical_flow.py).
ManAgent est 100% agnostique et utilise le schéma universel 'managent_flow'.
"""

from core.skills.canonical_flow import (
    CanonicalFlowConverter,
    FloConverterInterface,
)

__all__ = [
    "CanonicalFlowConverter",
    "FloConverterInterface",
]

"""
core/markers/novelty_marker.py
==============================
Marqueur : le MissionCompactor a estimé que la mission est nouvelle.
"""

from core.markers.base import BaseMarker
from typing import Dict, Any


class NoveltyMarker(BaseMarker):
    def __init__(self):
        super().__init__(name="novelty", weight=3)

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return context.get("is_novel", False)
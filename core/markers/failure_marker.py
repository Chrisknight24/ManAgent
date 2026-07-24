"""
core/markers/failure_marker.py
==============================
Marqueur : la mission a échoué.
"""

from core.markers.base import BaseMarker
from typing import Dict, Any


class FailureMarker(BaseMarker):
    def __init__(self):
        super().__init__(name="failure", weight=3)

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return context.get("status") == "failed"
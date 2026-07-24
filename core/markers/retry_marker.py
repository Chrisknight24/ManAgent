"""
core/markers/retry_marker.py
=============================
Marqueur : la mission a nécessité au moins un retry.
"""

from core.markers.base import BaseMarker
from typing import Dict, Any


class RetryMarker(BaseMarker):
    def __init__(self):
        super().__init__(name="retry", weight=2)

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return context.get("execution_attempt", 0) > 1
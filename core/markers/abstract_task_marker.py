"""
core/markers/abstract_task_marker.py
====================================
Marqueur : le plan contenait au moins une abstract_task.
"""

from core.markers.base import BaseMarker
from typing import Dict, Any


class AbstractTaskMarker(BaseMarker):
    def __init__(self):
        super().__init__(name="abstract_task", weight=1)

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return context.get("has_abstract_task", False)
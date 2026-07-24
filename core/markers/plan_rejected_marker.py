"""
core/markers/plan_rejected_marker.py
====================================
Marqueur : le plan a été rejeté au moins une fois (validation ou supervisor).
"""

from core.markers.base import BaseMarker
from typing import Dict, Any


class PlanRejectedMarker(BaseMarker):
    def __init__(self):
        super().__init__(name="plan_rejected", weight=2)

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return context.get("plan_rejected", False)
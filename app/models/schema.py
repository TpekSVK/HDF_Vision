"""Dataclasses for recipe serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class RecipeData:
    """Structure stored in ``regions.json`` for each recipe."""

    pose_enabled: bool = True
    regions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": list(self.regions),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RecipeData":
        return RecipeData(
            pose_enabled=bool(data["pose_enabled"]),
            regions=list(data.get("regions", [])),
        )

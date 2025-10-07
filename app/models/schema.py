from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from app.models.regions import Region


@dataclass
class RecipeDefinition:
    """Serialised reprezentácia receptu uloženého v regions.json."""

    pose_enabled: bool = True
    regions: List[Region] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": [r.to_dict() if isinstance(r, Region) else r for r in self.regions],
        }

    @staticmethod
    def from_dict(data: dict) -> "RecipeDefinition":
        if "pose_enabled" not in data:
            raise KeyError("Recept neobsahuje pole 'pose_enabled'.")
        regions_raw: Iterable = data.get("regions", [])
        regions = [r if isinstance(r, Region) else Region.from_dict(r) for r in regions_raw]
        return RecipeDefinition(pose_enabled=bool(data["pose_enabled"]), regions=regions)

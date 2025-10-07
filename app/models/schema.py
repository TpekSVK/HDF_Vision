from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Literal, Optional, Dict, Any

import numpy as np

from app.models.regions import Region

ToolKind = Literal["SSIM", "TemplateMatch", "AbsDiff"]


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> Dict[str, int]:
        return {"x": int(self.x), "y": int(self.y), "w": int(self.w), "h": int(self.h)}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Rect":
        return Rect(x=int(data["x"]), y=int(data["y"]), w=int(data["w"]), h=int(data["h"]))


@dataclass
class ToolNode:
    id: str
    kind: ToolKind
    name: str
    roi: Rect
    thresholds: Dict[str, Any]
    enabled: bool = True
    ignore_mask_path: Optional[str] = None
    ignore_mask: Optional[np.ndarray] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "enabled": bool(self.enabled),
            "roi": self.roi.to_dict(),
            "thresholds": dict(self.thresholds),
            "ignore_mask_path": self.ignore_mask_path,
        }

    @staticmethod
    def from_dict(data: dict) -> "ToolNode":
        roi_raw = data.get("roi")
        if not isinstance(roi_raw, dict):
            raise ValueError("ToolNode.roi musí byť dict.")
        thresholds_raw = data.get("thresholds") or {}
        if not isinstance(thresholds_raw, dict):
            raise ValueError("ToolNode.thresholds musí byť dict.")
        return ToolNode(
            id=str(data["id"]),
            kind=data["kind"],
            name=str(data.get("name", data["kind"])),
            enabled=bool(data.get("enabled", True)),
            roi=Rect.from_dict(roi_raw),
            thresholds=dict(thresholds_raw),
            ignore_mask_path=data.get("ignore_mask_path"),
        )


@dataclass
class RecipeDefinition:
    """Serialised reprezentácia receptu uloženého v regions.json."""

    pose_enabled: bool = True
    regions: List[Region] = field(default_factory=list)
    tools: List[ToolNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": [r.to_dict() if isinstance(r, Region) else r for r in self.regions],
            "tools": [t.to_dict() for t in self.tools],
        }

    @staticmethod
    def from_dict(data: dict) -> "RecipeDefinition":
        if "pose_enabled" not in data:
            raise KeyError("Recept neobsahuje pole 'pose_enabled'.")
        regions_raw: Iterable = data.get("regions", [])
        regions = [r if isinstance(r, Region) else Region.from_dict(r) for r in regions_raw]
        tools_raw: Iterable = data.get("tools", [])
        tools = [t if isinstance(t, ToolNode) else ToolNode.from_dict(t) for t in tools_raw]
        return RecipeDefinition(
            pose_enabled=bool(data["pose_enabled"]),
            regions=regions,
            tools=tools,
        )

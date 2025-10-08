"""Dataclasses for recipe serialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np


@dataclass(slots=True)
class ToolParams:
    """Container for tool specific parameters."""

    values: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = {}
        else:
            self.values = dict(self.values)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.values)

    @classmethod
    def from_obj(cls, obj: Any | None) -> "ToolParams":
        if isinstance(obj, ToolParams):
            return cls(obj.values)
        if obj is None:
            return cls()
        if isinstance(obj, dict):
            return cls(obj)
        return cls(dict(obj))

    def copy(self) -> "ToolParams":
        return ToolParams(deepcopy(self.values))


@dataclass(slots=True)
class ToolThresholds:
    """Container for thresholds with float coercion."""

    values: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = {}
        else:
            self.values = {str(k): float(v) for k, v in dict(self.values).items()}

    def to_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in self.values.items()}

    @classmethod
    def from_obj(cls, obj: Any | None) -> "ToolThresholds":
        if isinstance(obj, ToolThresholds):
            return cls(obj.values)
        if obj is None:
            return cls()
        if isinstance(obj, dict):
            return cls(obj)
        return cls(dict(obj))

    def copy(self) -> "ToolThresholds":
        return ToolThresholds(deepcopy(self.values))


@dataclass(slots=True)
class ToolRoi:
    """Region of interest descriptor for a tool."""

    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}
        else:
            self.data = dict(self.data)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.data)

    @classmethod
    def from_obj(cls, obj: Any | None) -> "ToolRoi":
        if isinstance(obj, ToolRoi):
            return cls(obj.data)
        if obj is None:
            return cls()
        if isinstance(obj, dict):
            return cls(obj)
        return cls(dict(obj))

    def copy(self) -> "ToolRoi":
        return ToolRoi(deepcopy(self.data))


@dataclass(slots=True)
class ToolMask:
    """Mask used to ignore pixels within a tool."""

    value: Optional[np.ndarray | Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if isinstance(self.value, list):
            self.value = np.asarray(self.value)

    def to_dict(self) -> Optional[Dict[str, Any] | List[Any]]:
        if self.value is None:
            return None
        if isinstance(self.value, np.ndarray):
            return {
                "type": "ndarray",
                "dtype": str(self.value.dtype),
                "shape": list(self.value.shape),
                "data": self.value.flatten().tolist(),
            }
        return deepcopy(self.value)

    @classmethod
    def from_obj(cls, obj: Any | None) -> "ToolMask":
        if isinstance(obj, ToolMask):
            return cls(obj.value.copy() if isinstance(obj.value, np.ndarray) else deepcopy(obj.value))
        if obj is None:
            return cls(None)
        if isinstance(obj, np.ndarray):
            return cls(obj)
        if isinstance(obj, dict):
            if obj.get("type") == "ndarray" and "data" in obj and "shape" in obj:
                dtype = obj.get("dtype", "float32")
                arr = np.asarray(obj["data"], dtype=dtype)
                try:
                    arr = arr.reshape(tuple(obj["shape"]))
                except Exception:
                    arr = arr.reshape(-1)
                return cls(arr)
            return cls(deepcopy(obj))
        if isinstance(obj, (list, tuple)):
            return cls(np.asarray(obj))
        return cls(obj)

    def copy(self) -> "ToolMask":
        if isinstance(self.value, np.ndarray):
            return ToolMask(self.value.copy())
        return ToolMask(deepcopy(self.value))


@dataclass(slots=True)
class Tool:
    """Representation of a processing tool within a recipe pipeline."""

    type: str = ""
    name: str = ""
    enabled: bool = True
    order: int = 0
    roi: ToolRoi = field(default_factory=ToolRoi)
    ignore_mask: ToolMask = field(default_factory=ToolMask)
    params: ToolParams = field(default_factory=ToolParams)
    thresholds: ToolThresholds = field(default_factory=ToolThresholds)

    def __post_init__(self) -> None:
        self.type = str(self.type)
        self.name = str(self.name)
        self.enabled = bool(self.enabled)
        self.order = int(self.order)
        self.roi = ToolRoi.from_obj(self.roi)
        self.ignore_mask = ToolMask.from_obj(self.ignore_mask)
        self.params = ToolParams.from_obj(self.params)
        self.thresholds = ToolThresholds.from_obj(self.thresholds)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "enabled": bool(self.enabled),
            "order": int(self.order),
            "roi": self.roi.to_dict(),
            "ignore_mask": self.ignore_mask.to_dict(),
            "params": self.params.to_dict(),
            "thresholds": self.thresholds.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | "Tool") -> "Tool":
        if isinstance(data, Tool):
            return data.copy()
        if not isinstance(data, dict):
            raise TypeError("Tool.from_dict expects a dict or Tool instance")
        return cls(
            type=data.get("type", ""),
            name=data.get("name", ""),
            enabled=data.get("enabled", True),
            order=data.get("order", 0),
            roi=ToolRoi.from_obj(data.get("roi")),
            ignore_mask=ToolMask.from_obj(data.get("ignore_mask")),
            params=ToolParams.from_obj(data.get("params")),
            thresholds=ToolThresholds.from_obj(data.get("thresholds")),
        )

    def copy(self) -> "Tool":
        return Tool(
            type=self.type,
            name=self.name,
            enabled=self.enabled,
            order=self.order,
            roi=self.roi.copy(),
            ignore_mask=self.ignore_mask.copy(),
            params=self.params.copy(),
            thresholds=self.thresholds.copy(),
        )

    def with_order(self, order: int) -> "Tool":
        tool = self.copy()
        tool.order = int(order)
        return tool


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


@dataclass(slots=True)
class RecipeV2:
    """Extended recipe structure including tool pipeline information."""

    pose_enabled: bool = True
    regions: List[Dict[str, Any]] = field(default_factory=list)
    tools: List[Tool] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.pose_enabled = bool(self.pose_enabled)
        self.regions = [dict(r) for r in self.regions]
        converted: List[Tool] = []
        for tool in self.tools:
            if isinstance(tool, Tool):
                converted.append(tool.copy())
            else:
                converted.append(Tool.from_dict(tool))
        converted.sort(key=lambda t: t.order)
        self.tools = converted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": [dict(r) for r in self.regions],
            "tools": [t.to_dict() for t in self.tools],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "RecipeV2":
        if not data:
            return cls()
        return cls(
            pose_enabled=data.get("pose_enabled", True),
            regions=data.get("regions", []),
            tools=data.get("tools", []),
        )

    @classmethod
    def from_recipe_data(cls, recipe: RecipeData) -> "RecipeV2":
        return cls(
            pose_enabled=recipe.pose_enabled,
            regions=recipe.regions,
            tools=[],
        )

    def copy(self) -> "RecipeV2":
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=[tool.copy() for tool in self.tools],
        )

    def with_tools(self, tools: Sequence[Tool]) -> "RecipeV2":
        new_tools = [tool.copy() for tool in tools]
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=new_tools,
        )

    def iter_tools(self) -> Iterable[Tool]:
        return tuple(tool.copy() for tool in self.tools)

"""Dataclasses for recipe serialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Literal, cast


@dataclass(slots=True)
class ToolSchemaField:
    """Specification describing a single configurable field."""

    name: str
    type: str
    default: Any | None = None
    label: Optional[str] = None
    description: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    required: bool = False
    choices: Tuple[Tuple[Any, Any], ...] = ()


@dataclass(slots=True)
class ToolSchema:
    """Schema definition describing parameters and thresholds."""

    params: Tuple[ToolSchemaField, ...] = ()
    thresholds: Tuple[ToolSchemaField, ...] = ()


@dataclass(slots=True)
class ToolMetricSpec:
    """Descriptor for metric emitted by a tool."""

    key: str
    unit: Optional[str] = None
    priority: int = 0
    description: str = ""


@dataclass(slots=True)
class ToolMetaDefinition:
    """Meta information attached to tool definitions."""

    supports_roi: bool = False
    supports_ignore_mask: bool = False
    schema: ToolSchema = field(default_factory=ToolSchema)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDefinition:
    """High level descriptor for a tool available in the system."""

    type_id: str
    name: str
    description: str
    category: str = "General"
    deprecated: bool = False
    meta: ToolMetaDefinition = field(default_factory=ToolMetaDefinition)
    metrics_spec: Tuple[ToolMetricSpec, ...] = ()

import numpy as np

from app.utils import imaging


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
        result: Dict[str, Any] = {}
        for key, value in self.values.items():
            if isinstance(value, ToolRoi):
                rect_dict = value.to_dict()
                result[key] = rect_dict if rect_dict else None
            else:
                result[key] = value
        return result

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
            return

        if not isinstance(self.data, dict):
            self.data = dict(self.data)

        keys = {"x", "y", "w", "h"}
        if keys.issubset(self.data.keys()):
            try:
                x = int(round(float(self.data["x"])))
                y = int(round(float(self.data["y"])))
                w = int(round(float(self.data["w"])))
                h = int(round(float(self.data["h"])))
            except Exception:
                self.data = {}
                return
            if w <= 0 or h <= 0:
                self.data = {}
                return
            self.data = {"x": x, "y": y, "w": w, "h": h}
        else:
            self.data = {}

    def rect(self) -> Optional[Tuple[int, int, int, int]]:
        if not self.data:
            return None
        try:
            x = int(self.data["x"])
            y = int(self.data["y"])
            w = int(self.data["w"])
            h = int(self.data["h"])
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return x, y, w, h

    def set_rect(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        if rect is None:
            self.data = {}
            return
        x, y, w, h = rect
        self.data = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}

    def to_dict(self) -> Dict[str, Any]:
        rect = self.rect()
        if rect is None:
            return {}
        x, y, w, h = rect
        return {"x": x, "y": y, "w": w, "h": h}

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

    value: Optional[np.ndarray] = None

    def __post_init__(self) -> None:
        if self.value is None:
            return
        if isinstance(self.value, np.ndarray):
            arr = self.value
        elif isinstance(self.value, (list, tuple)):
            arr = np.asarray(self.value)
        elif isinstance(self.value, dict):
            arr = imaging.decode_mask_from_blob(self.value)
            if arr is None and self.value.get("type") == "ndarray":
                try:
                    dtype = self.value.get("dtype", "uint8")
                    data = np.asarray(self.value.get("data", []), dtype=dtype)
                    shape = tuple(self.value.get("shape", []))
                    arr = data.reshape(shape)
                except Exception:
                    arr = None
        else:
            arr = np.asarray(self.value)

        if arr is None:
            self.value = None
            return

        if arr.ndim == 3:
            arr = arr[:, :, 0]
        if arr.ndim != 2:
            raise ValueError("Mask must be 2D")
        self.value = arr.astype(np.uint8, copy=False)

    def to_dict(self) -> Optional[Dict[str, Any]]:
        if self.value is None:
            return None
        return imaging.encode_mask_to_blob(self.value)

    @classmethod
    def from_obj(cls, obj: Any | None) -> "ToolMask":
        if isinstance(obj, ToolMask):
            return cls(obj.value.copy() if obj.value is not None else None)
        if obj is None:
            return cls(None)
        if isinstance(obj, np.ndarray):
            return cls(obj)
        if isinstance(obj, dict):
            decoded = imaging.decode_mask_from_blob(obj)
            if decoded is not None:
                return cls(decoded)
            if obj.get("type") == "ndarray" and "data" in obj and "shape" in obj:
                dtype = obj.get("dtype", "uint8")
                arr = np.asarray(obj["data"], dtype=dtype)
                try:
                    arr = arr.reshape(tuple(obj["shape"]))
                except Exception:
                    arr = arr.reshape(-1)
                return cls(arr)
            return cls(None)
        if isinstance(obj, (list, tuple)):
            return cls(np.asarray(obj))
        return cls(None)

    def copy(self) -> "ToolMask":
        if self.value is None:
            return ToolMask(None)
        return ToolMask(self.value.copy())


DEFAULT_VIEW_ID = "default"


@dataclass(slots=True)
class RecipeView:
    """Descriptor representing a single view within a multi-view recipe."""

    id: str
    name: str = ""
    golden_path: str = ""
    camera_profile: Optional[str] = None
    settle_ms: int = 0

    def __post_init__(self) -> None:
        view_id = str(self.id or "").strip() or DEFAULT_VIEW_ID
        self.id = view_id
        self.name = str(self.name or view_id)
        self.golden_path = str(self.golden_path or "")
        self.camera_profile = (
            str(self.camera_profile) if self.camera_profile is not None else None
        )
        try:
            self.settle_ms = int(self.settle_ms)
        except Exception:
            self.settle_ms = 0

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "golden_path": self.golden_path,
            "settle_ms": int(self.settle_ms),
        }
        if self.camera_profile is not None:
            data["camera_profile"] = self.camera_profile
        return data


AggregationMode = Literal["AND", "OR", "WEIGHTED"]


@dataclass(slots=True)
class RecipeAggregation:
    """Configuration describing how per-view results are aggregated."""

    mode: AggregationMode = "AND"
    weights: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        raw_mode = str(self.mode or "AND").upper()
        if raw_mode not in {"AND", "OR", "WEIGHTED"}:
            raw_mode = "AND"
        self.mode = cast(AggregationMode, raw_mode)  # type: ignore[assignment]

        normalized: Dict[str, float] = {}
        for key, value in dict(self.weights or {}).items():
            try:
                weight = float(value)
            except Exception:
                continue
            if weight < 0.0:
                continue
            normalized[str(key)] = weight
        self.weights = normalized

    def weight_for(self, view_id: str, default: float = 1.0) -> float:
        return float(self.weights.get(view_id, default))


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
    template_roi: ToolRoi = field(default_factory=ToolRoi)
    view_id: str = ""

    def __post_init__(self) -> None:
        self.type = str(self.type)
        self.name = str(self.name)
        self.enabled = bool(self.enabled)
        self.order = int(self.order)
        self.roi = ToolRoi.from_obj(self.roi)
        self.ignore_mask = ToolMask.from_obj(self.ignore_mask)
        self.params = ToolParams.from_obj(self.params)
        self.thresholds = ToolThresholds.from_obj(self.thresholds)
        has_template_key = isinstance(self.params.values, dict) and "template_roi" in self.params.values
        raw_template_roi = None
        if has_template_key:
            raw_template_roi = self.params.values.get("template_roi")
        if raw_template_roi is None and isinstance(self.template_roi, ToolRoi):
            raw_template_roi = self.template_roi.to_dict()
        self.template_roi = ToolRoi.from_obj(raw_template_roi)
        template_roi_dict = self.template_roi.to_dict()
        if isinstance(self.params.values, dict) and (has_template_key or template_roi_dict):
            if template_roi_dict:
                self.params.values["template_roi"] = template_roi_dict
            elif has_template_key:
                self.params.values["template_roi"] = None
        self.view_id = str(self.view_id or "").strip()

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
            "template_roi": self.template_roi.to_dict() if self.template_roi.rect() else None,
            "view_id": self.view_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | "Tool") -> "Tool":
        if isinstance(data, Tool):
            return data.copy()
        if not isinstance(data, dict):
            raise TypeError("Tool.from_dict expects a dict or Tool instance")
        params = ToolParams.from_obj(data.get("params"))
        template_roi_data = data.get("template_roi")
        if template_roi_data is not None and isinstance(params.values, dict):
            params.values.setdefault("template_roi", template_roi_data)
        return cls(
            type=data.get("type", ""),
            name=data.get("name", ""),
            enabled=data.get("enabled", True),
            order=data.get("order", 0),
            roi=ToolRoi.from_obj(data.get("roi")),
            ignore_mask=ToolMask.from_obj(data.get("ignore_mask")),
            params=params,
            thresholds=ToolThresholds.from_obj(data.get("thresholds")),
            template_roi=ToolRoi.from_obj(template_roi_data),
            view_id=str(data.get("view_id", "") or ""),
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
            template_roi=self.template_roi.copy(),
            view_id=self.view_id,
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
    on_locator_failure: Literal["fail", "continue_without_alignment"] = (
        "continue_without_alignment"
    )
    export_artifacts: bool = False
    views: List[RecipeView] = field(default_factory=list)
    aggregation: RecipeAggregation = field(default_factory=RecipeAggregation)

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

        normalized_views: List[RecipeView] = []
        for view in self.views:
            if isinstance(view, RecipeView):
                normalized_views.append(RecipeView(
                    id=view.id,
                    name=view.name,
                    golden_path=view.golden_path,
                    camera_profile=view.camera_profile,
                    settle_ms=view.settle_ms,
                ))
            else:
                normalized_views.append(RecipeView(**dict(view)))
        if not normalized_views:
            normalized_views = [
                RecipeView(id=DEFAULT_VIEW_ID, name="Default View", golden_path="golden.png")
            ]
        self.views = normalized_views

        valid_view_ids = {view.id for view in self.views}
        default_view_id = self.views[0].id
        for tool in self.tools:
            if not tool.view_id or tool.view_id not in valid_view_ids:
                tool.view_id = default_view_id

        if not isinstance(self.aggregation, RecipeAggregation):
            raw_agg: Dict[str, Any]
            if isinstance(self.aggregation, dict):
                raw_agg = dict(self.aggregation)
            elif hasattr(self.aggregation, "items"):
                raw_agg = dict(self.aggregation.items())  # type: ignore[call-arg]
            else:
                raw_agg = {}
            self.aggregation = RecipeAggregation(**raw_agg)
        else:
            self.aggregation = RecipeAggregation(
                mode=self.aggregation.mode,
                weights=dict(self.aggregation.weights),
            )

        policy = str(self.on_locator_failure or "").lower()
        if policy not in {"fail", "continue_without_alignment"}:
            policy = "continue_without_alignment"
        self.on_locator_failure = (
            "fail" if policy == "fail" else "continue_without_alignment"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": [dict(r) for r in self.regions],
            "tools": [t.to_dict() for t in self.tools],
            "on_locator_failure": self.on_locator_failure,
            "export_artifacts": bool(self.export_artifacts),
            "views": [view.to_dict() for view in self.views],
            "aggregation": {
                "mode": self.aggregation.mode,
                "weights": dict(self.aggregation.weights),
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "RecipeV2":
        if not data:
            return cls()
        return cls(
            pose_enabled=data.get("pose_enabled", True),
            regions=data.get("regions", []),
            tools=data.get("tools", []),
            on_locator_failure=data.get(
                "on_locator_failure", "continue_without_alignment"
            ),
            export_artifacts=bool(data.get("export_artifacts", False)),
            views=data.get("views", []),
            aggregation=data.get("aggregation", {}),
        )

    @classmethod
    def from_recipe_data(cls, recipe: RecipeData) -> "RecipeV2":
        return cls(
            pose_enabled=recipe.pose_enabled,
            regions=recipe.regions,
            tools=[],
            on_locator_failure="continue_without_alignment",
            export_artifacts=False,
            views=[RecipeView(id=DEFAULT_VIEW_ID, name="Default View", golden_path="golden.png")],
        )

    def copy(self) -> "RecipeV2":
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=[tool.copy() for tool in self.tools],
            on_locator_failure=self.on_locator_failure,
            export_artifacts=self.export_artifacts,
            views=[RecipeView(
                id=view.id,
                name=view.name,
                golden_path=view.golden_path,
                camera_profile=view.camera_profile,
                settle_ms=view.settle_ms,
            ) for view in self.views],
            aggregation=RecipeAggregation(
                mode=self.aggregation.mode,
                weights=dict(self.aggregation.weights),
            ),
        )

    def with_tools(self, tools: Sequence[Tool]) -> "RecipeV2":
        new_tools = [tool.copy() for tool in tools]
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=new_tools,
            on_locator_failure=self.on_locator_failure,
            export_artifacts=self.export_artifacts,
            views=[
                RecipeView(
                    id=view.id,
                    name=view.name,
                    golden_path=view.golden_path,
                    camera_profile=view.camera_profile,
                    settle_ms=view.settle_ms,
                )
                for view in self.views
            ],
            aggregation=RecipeAggregation(
                mode=self.aggregation.mode,
                weights=dict(self.aggregation.weights),
            ),
        )

    def iter_tools(self) -> Iterable[Tool]:
        return tuple(tool.copy() for tool in self.tools)

    @property
    def primary_view_id(self) -> str:
        return self.views[0].id if self.views else DEFAULT_VIEW_ID

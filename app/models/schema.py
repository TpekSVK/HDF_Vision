"""Dataclasses for recipe serialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Literal


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
class CameraProfile:
    """Descriptor of per-step camera acquisition settings."""

    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    exposure: Optional[float] = None
    gain: Optional[float] = None
    pixel_format: Optional[str] = None

    def __post_init__(self) -> None:
        self.width = int(self.width) if self.width is not None else None
        self.height = int(self.height) if self.height is not None else None
        self.fps = float(self.fps) if self.fps is not None else None
        self.exposure = float(self.exposure) if self.exposure is not None else None
        self.gain = float(self.gain) if self.gain is not None else None
        self.pixel_format = str(self.pixel_format) if self.pixel_format else None

    def copy(self) -> "CameraProfile":
        return CameraProfile(
            width=self.width,
            height=self.height,
            fps=self.fps,
            exposure=self.exposure,
            gain=self.gain,
            pixel_format=self.pixel_format,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.width is not None:
            data["width"] = int(self.width)
        if self.height is not None:
            data["height"] = int(self.height)
        if self.fps is not None:
            data["fps"] = float(self.fps)
        if self.exposure is not None:
            data["exposure"] = float(self.exposure)
        if self.gain is not None:
            data["gain"] = float(self.gain)
        if self.pixel_format:
            data["pixel_format"] = str(self.pixel_format)
        return data

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "CameraProfile | None":
        if not data:
            return None
        return cls(
            width=data.get("width"),
            height=data.get("height"),
            fps=data.get("fps"),
            exposure=data.get("exposure"),
            gain=data.get("gain"),
            pixel_format=data.get("pixel_format"),
        )


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
class MultiViewStep:
    """Configuration describing a single acquisition step within a recipe."""

    step_id: str
    name: str
    order: int = 0
    golden_path: str = "golden.png"
    pose_enabled: bool = True
    regions: List[Dict[str, Any]] = field(default_factory=list)
    thresholds: ToolThresholds = field(default_factory=ToolThresholds)
    camera_profile: CameraProfile | None = None
    settle_ms: Optional[int] = None

    def __post_init__(self) -> None:
        self.step_id = str(self.step_id or "")
        self.name = str(self.name or "")
        self.order = int(self.order)
        self.golden_path = str(self.golden_path or "golden.png")
        self.pose_enabled = bool(self.pose_enabled)
        self.regions = [dict(region) for region in self.regions]
        if isinstance(self.thresholds, ToolThresholds):
            self.thresholds = self.thresholds.copy()
        else:
            self.thresholds = ToolThresholds.from_obj(self.thresholds)
        if isinstance(self.camera_profile, CameraProfile):
            self.camera_profile = self.camera_profile.copy()
        else:
            self.camera_profile = CameraProfile.from_dict(self.camera_profile)
        if self.settle_ms is not None:
            try:
                self.settle_ms = int(self.settle_ms)
            except Exception:
                self.settle_ms = None

    def copy(self) -> "MultiViewStep":
        return MultiViewStep(
            step_id=self.step_id,
            name=self.name,
            order=self.order,
            golden_path=self.golden_path,
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(region) for region in self.regions],
            thresholds=self.thresholds.copy(),
            camera_profile=self.camera_profile.copy() if self.camera_profile else None,
            settle_ms=self.settle_ms,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "step_id": self.step_id,
            "name": self.name,
            "order": int(self.order),
            "golden_path": self.golden_path,
            "pose_enabled": bool(self.pose_enabled),
            "regions": [deepcopy(region) for region in self.regions],
            "thresholds": self.thresholds.to_dict(),
        }
        if self.camera_profile:
            data["camera_profile"] = self.camera_profile.to_dict()
        if self.settle_ms is not None:
            data["settle_ms"] = int(self.settle_ms)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MultiViewStep":
        return cls(
            step_id=data.get("step_id", ""),
            name=data.get("name", ""),
            order=data.get("order", 0),
            golden_path=data.get("golden_path", "golden.png"),
            pose_enabled=data.get("pose_enabled", True),
            regions=data.get("regions", []),
            thresholds=ToolThresholds.from_obj(data.get("thresholds")),
            camera_profile=CameraProfile.from_dict(data.get("camera_profile")),
            settle_ms=data.get("settle_ms"),
        )


@dataclass(slots=True)
class MultiViewConfig:
    """Container describing multi-view execution parameters."""

    steps: List[MultiViewStep] = field(default_factory=list)
    aggregation: Literal["AND", "OR", "WEIGHTED"] = "AND"
    weights: Dict[str, float] = field(default_factory=dict)
    weighted_threshold: float = 0.5

    def __post_init__(self) -> None:
        normalized_steps: List[MultiViewStep] = []
        for step in self.steps:
            if isinstance(step, MultiViewStep):
                normalized_steps.append(step.copy())
            else:
                normalized_steps.append(MultiViewStep.from_dict(step))
        normalized_steps.sort(key=lambda entry: (entry.order, entry.step_id))
        self.steps = normalized_steps

        aggregation = str(self.aggregation or "AND").upper()
        if aggregation not in {"AND", "OR", "WEIGHTED"}:
            aggregation = "AND"
        self.aggregation = aggregation  # type: ignore[assignment]

        weights_dict: Dict[str, float] = {}
        for key, value in (self.weights or {}).items():
            try:
                weights_dict[str(key)] = float(value)
            except Exception:
                continue
        self.weights = weights_dict

        try:
            threshold = float(self.weighted_threshold)
        except Exception:
            threshold = 0.5
        self.weighted_threshold = max(0.0, min(1.0, threshold))

    def copy(self) -> "MultiViewConfig":
        return MultiViewConfig(
            steps=[step.copy() for step in self.steps],
            aggregation=self.aggregation,
            weights=dict(self.weights),
            weighted_threshold=self.weighted_threshold,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "aggregation": self.aggregation,
            "weights": {k: float(v) for k, v in self.weights.items()},
            "weighted_threshold": float(self.weighted_threshold),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "MultiViewConfig":
        if not data:
            return cls()
        return cls(
            steps=data.get("steps", []),
            aggregation=data.get("aggregation", "AND"),
            weights=data.get("weights", {}),
            weighted_threshold=data.get("weighted_threshold", 0.5),
        )

    def iter_steps(self) -> Iterable[MultiViewStep]:
        return tuple(step.copy() for step in self.steps)

    def effective_weights(self, step_ids: Iterable[str]) -> Dict[str, float]:
        ids = list(step_ids)
        if not ids:
            return {}
        if not self.weights:
            return {step_id: 1.0 for step_id in ids}
        mapped = {step_id: float(self.weights.get(step_id, 0.0)) for step_id in ids}
        if all(weight <= 0.0 for weight in mapped.values()):
            return {step_id: 1.0 for step_id in ids}
        return mapped


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
    multi_view: MultiViewConfig = field(default_factory=MultiViewConfig)

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

        policy = str(self.on_locator_failure or "").lower()
        if policy not in {"fail", "continue_without_alignment"}:
            policy = "continue_without_alignment"
        self.on_locator_failure = (
            "fail" if policy == "fail" else "continue_without_alignment"
        )

        if isinstance(self.multi_view, MultiViewConfig):
            self.multi_view = self.multi_view.copy()
        else:
            self.multi_view = MultiViewConfig.from_dict(self.multi_view)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pose_enabled": bool(self.pose_enabled),
            "regions": [dict(r) for r in self.regions],
            "tools": [t.to_dict() for t in self.tools],
            "on_locator_failure": self.on_locator_failure,
            "export_artifacts": bool(self.export_artifacts),
            "multi_view": self.multi_view.to_dict(),
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
            multi_view=MultiViewConfig.from_dict(data.get("multi_view")),
        )

    @classmethod
    def from_recipe_data(cls, recipe: RecipeData) -> "RecipeV2":
        return cls(
            pose_enabled=recipe.pose_enabled,
            regions=recipe.regions,
            tools=[],
            on_locator_failure="continue_without_alignment",
            export_artifacts=False,
        )

    def copy(self) -> "RecipeV2":
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=[tool.copy() for tool in self.tools],
            on_locator_failure=self.on_locator_failure,
            export_artifacts=self.export_artifacts,
            multi_view=self.multi_view.copy(),
        )

    def with_tools(self, tools: Sequence[Tool]) -> "RecipeV2":
        new_tools = [tool.copy() for tool in tools]
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=new_tools,
            on_locator_failure=self.on_locator_failure,
            export_artifacts=self.export_artifacts,
            multi_view=self.multi_view.copy(),
        )

    def iter_tools(self) -> Iterable[Tool]:
        return tuple(tool.copy() for tool in self.tools)

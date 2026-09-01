"""Dataclasses for recipe serialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Literal, cast


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
from app.utils.external_source import normalize_external_input, normalize_external_source


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
        self.view_id = str(self.view_id or "")

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
            "view_id": self.view_id or None,
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
            view_id=str(data.get("view_id", "")),
        )

    def copy(self, *, copy_mask: bool = True) -> "Tool":
        return Tool(
            type=self.type,
            name=self.name,
            enabled=self.enabled,
            order=self.order,
            roi=self.roi.copy(),
            ignore_mask=self.ignore_mask.copy() if copy_mask else ToolMask(self.ignore_mask.value),
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
class RecipeAggregation:
    """Aggregation policy for combining per-view results."""

    mode: Literal["AND", "OR", "WEIGHTED"] = "AND"
    weights: Dict[str, float] = field(default_factory=dict)
    fail_fast: bool = False

    def __post_init__(self) -> None:
        mode = str(self.mode or "AND").upper()
        if mode not in {"AND", "OR", "WEIGHTED"}:
            mode = "AND"
        self.mode = mode

        self.fail_fast = bool(self.fail_fast)

        normalized: Dict[str, float] = {}
        if mode == "WEIGHTED":
            for key, value in dict(self.weights).items():
                try:
                    normalized[str(key)] = float(value)
                except Exception:
                    continue
        self.weights = normalized

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "weights": dict(self.weights) if self.mode == "WEIGHTED" else {},
            "fail_fast": bool(self.fail_fast),
        }

    @classmethod
    def from_obj(cls, obj: Any | None) -> "RecipeAggregation":
        if isinstance(obj, RecipeAggregation):
            return obj.copy()
        if obj is None:
            return cls()
        if isinstance(obj, dict):
            return cls(
                mode=obj.get("mode", "AND"),
                weights=obj.get("weights", {}),
                fail_fast=obj.get("fail_fast", False),
            )
        return cls(
            mode=getattr(obj, "mode", "AND"),
            weights=getattr(obj, "weights", {}),
            fail_fast=getattr(obj, "fail_fast", False),
        )

    def copy(self) -> "RecipeAggregation":
        return RecipeAggregation(
            mode=self.mode,
            weights=dict(self.weights),
            fail_fast=self.fail_fast,
        )

    def align_with_views(self, views: Sequence["RecipeView"]) -> "RecipeAggregation":
        if self.mode != "WEIGHTED":
            self.weights = {}
            return self

        aligned: Dict[str, float] = {}
        for view in views:
            weight = float(self.weights.get(view.id, 0.0)) if view.id else 0.0
            aligned[view.id] = weight

        if views and all(weight <= 0.0 for weight in aligned.values()):
            default_weight = 1.0 / float(len(views))
            aligned = {view.id: default_weight for view in views}

        self.weights = aligned
        return self

    @staticmethod
    def _normalize_status(value: str | None) -> Literal["ok", "nok", "warn"]:
        normalized = str(value or "").strip().lower()
        if normalized not in {"ok", "nok", "warn"}:
            return "nok"
        return cast(Literal["ok", "nok", "warn"], normalized)

    def aggregate_statuses(
        self, statuses: Mapping[str, str | None]
    ) -> Literal["ok", "nok", "warn"]:
        """Aggregate per-view statuses into a single verdict."""

        normalized: dict[str, Literal["ok", "nok", "warn"]] = {
            key: self._normalize_status(value)
            for key, value in statuses.items()
            if key
        }

        if not normalized:
            return "ok"

        if self.mode == "OR":
            priority = {"ok": 2, "warn": 1, "nok": 0}
            best = "nok"
            for status in normalized.values():
                if priority[status] > priority[best]:
                    best = status
            return cast(Literal["ok", "nok", "warn"], best)

        if self.mode == "WEIGHTED" and any(self.weights.values()):
            score_map = {"ok": 1.0, "warn": 0.5, "nok": 0.0}
            total_weight = 0.0
            weighted_score = 0.0
            for key, status in normalized.items():
                weight = float(self.weights.get(key, 0.0))
                if weight <= 0.0:
                    continue
                total_weight += weight
                weighted_score += weight * score_map[status]
            if total_weight <= 0.0:
                return self._aggregate_priority(normalized.values())
            score = weighted_score / total_weight
            if score >= 0.75:
                return "ok"
            if score >= 0.5:
                return "warn"
            return "nok"

        return self._aggregate_priority(normalized.values())

    @staticmethod
    def _aggregate_priority(
        statuses: Iterable[Literal["ok", "nok", "warn"]]
    ) -> Literal["ok", "nok", "warn"]:
        priority = {"ok": 0, "warn": 1, "nok": 2}
        worst = "ok"
        for status in statuses:
            if priority[status] > priority[worst]:
                worst = status
        return cast(Literal["ok", "nok", "warn"], worst)


@dataclass(slots=True)
class ViewCameraProfile:
    """Optional per-view overrides for camera configuration."""

    device_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    pixel_format: Optional[str] = None
    exposure_us: Optional[int] = None
    gain_db: Optional[float] = None
    gamma: Optional[float] = None
    brightness: Optional[float] = None
    sharpness: Optional[float] = None
    flash_mode: Optional[int] = None

    def __post_init__(self) -> None:
        if isinstance(self.device_id, str):
            self.device_id = self.device_id.strip() or None
        elif self.device_id is not None:
            self.device_id = str(self.device_id).strip() or None
        self.width = self._coerce_int(self.width)
        self.height = self._coerce_int(self.height)
        self.fps = self._coerce_int(self.fps)
        self.exposure_us = self._coerce_int(self.exposure_us)
        self.flash_mode = self._coerce_int(self.flash_mode)
        self.gain_db = self._coerce_float(self.gain_db)
        self.gamma = self._coerce_float(self.gamma)
        self.brightness = self._coerce_float(self.brightness)
        self.sharpness = self._coerce_float(self.sharpness)
        if isinstance(self.pixel_format, str):
            text = self.pixel_format.strip().upper()
            self.pixel_format = text or None
        elif self.pixel_format is not None:
            self.pixel_format = str(self.pixel_format).strip().upper() or None

    @staticmethod
    def _coerce_int(value: object) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _coerce_float(value: object) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except Exception:
            return None

    def is_empty(self) -> bool:
        return not any(
            value is not None
            for value in (
                self.width,
                self.height,
                self.fps,
                self.pixel_format,
                self.exposure_us,
                self.gain_db,
                self.gamma,
                self.brightness,
                self.sharpness,
                self.flash_mode,
                self.device_id,
            )
        )

    def copy(self) -> "ViewCameraProfile":
        return ViewCameraProfile(
            device_id=self.device_id,
            width=self.width,
            height=self.height,
            fps=self.fps,
            pixel_format=self.pixel_format,
            exposure_us=self.exposure_us,
            gain_db=self.gain_db,
            gamma=self.gamma,
            brightness=self.brightness,
            sharpness=self.sharpness,
            flash_mode=self.flash_mode,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.device_id:
            data["device_id"] = self.device_id
        if self.width is not None:
            data["width"] = int(self.width)
        if self.height is not None:
            data["height"] = int(self.height)
        if self.fps is not None:
            data["fps"] = int(self.fps)
        if self.pixel_format:
            data["pixel_format"] = self.pixel_format
        if self.exposure_us is not None:
            data["exposure_us"] = int(self.exposure_us)
        if self.gain_db is not None:
            data["gain_db"] = float(self.gain_db)
        if self.gamma is not None:
            data["gamma"] = float(self.gamma)
        if self.brightness is not None:
            data["brightness"] = float(self.brightness)
        if self.sharpness is not None:
            data["sharpness"] = float(self.sharpness)
        if self.flash_mode is not None:
            data["flash_mode"] = int(self.flash_mode)
        return data

    @classmethod
    def from_obj(
        cls, value: object
    ) -> ViewCameraProfile | str | None:
        if value is None or value == "":
            return None
        if isinstance(value, cls):
            return value.copy()
        if isinstance(value, dict):
            profile = cls(
                device_id=value.get("device_id"),
                width=value.get("width"),
                height=value.get("height"),
                fps=value.get("fps"),
                pixel_format=value.get("pixel_format"),
                exposure_us=value.get("exposure_us"),
                gain_db=value.get("gain_db"),
                gamma=value.get("gamma"),
                brightness=value.get("brightness"),
                sharpness=value.get("sharpness"),
                flash_mode=value.get("flash_mode"),
            )
            return None if profile.is_empty() else profile
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return None


@dataclass(slots=True)
class RecipeView:
    """Single viewpoint definition within a multi-view recipe."""

    id: str = ""
    name: str = ""
    golden_path: str = "golden.png"
    frame_source_view_id: Optional[str] = None
    camera_profile: Optional[ViewCameraProfile | str] = None
    settle_ms: Optional[int] = None
    flash_delay_ms: int = 0
    flash_pulse_ms: int = 200
    trigger_mode: Literal["timed", "external"] = "timed"
    external_trigger_mode: Optional[str] = None
    external_source: Optional[str] = None
    external_input: Optional[int] = None
    # Pre-canonical compatibility alias. New recipe JSON uses ``external_input``.
    external_request_input: Optional[int] = None
    trigger_interval_ms: Optional[int] = None
    trigger_gap_ms: Optional[float] = None
    image_rotation: int = 0
    tools: List[Tool] = field(default_factory=list)
    branch_enabled: bool = False
    branch_targets: dict[str, str] = field(default_factory=dict)
    branch_default_view_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.name = str(self.name or "").strip()
        self.golden_path = str(self.golden_path or "golden.png")
        self.frame_source_view_id = (
            str(self.frame_source_view_id).strip() or None
            if self.frame_source_view_id is not None
            else None
        )
        if self.frame_source_view_id == self.id:
            self.frame_source_view_id = None
        camera_profile = ViewCameraProfile.from_obj(self.camera_profile)
        if isinstance(camera_profile, ViewCameraProfile):
            self.camera_profile = camera_profile
        elif isinstance(camera_profile, str):
            self.camera_profile = camera_profile
        else:
            self.camera_profile = None

        if self.settle_ms is not None:
            try:
                self.settle_ms = int(self.settle_ms)
            except Exception:
                self.settle_ms = None
        try:
            self.flash_delay_ms = int(self.flash_delay_ms)
        except Exception:
            self.flash_delay_ms = 0
        if self.flash_delay_ms < 0:
            self.flash_delay_ms = 0
        try:
            self.flash_pulse_ms = int(self.flash_pulse_ms)
        except Exception:
            self.flash_pulse_ms = 200
        if self.flash_pulse_ms <= 0:
            self.flash_pulse_ms = 200

        mode = str(self.trigger_mode or "timed").strip().lower()
        mode = {
            "timer": "timed", "časovač": "timed", "manual": "external",
            "manual trigger": "external", "external trigger": "external",
            "externý signál": "external",
        }.get(mode, mode)
        if mode not in {"timed", "external"}:
            mode = "timed"
        self.trigger_mode = cast(Literal["timed", "external"], mode)

        raw_external_mode = (
            str(self.external_trigger_mode).strip().lower()
            if self.external_trigger_mode is not None
            else None
        )
        if self.trigger_mode != "external":
            self.external_trigger_mode = None
            self.external_source = None
            self.external_input = None
            self.external_request_input = None
        else:
            if not raw_external_mode:
                raw_external_mode = "sequential"
            if raw_external_mode not in {"sequential", "explicit"}:
                raw_external_mode = "sequential"
            self.external_trigger_mode = raw_external_mode
            # Missing source is a legacy Modbus recipe. Unknown/corrupt sources
            # remain invalid instead of being silently routed to another device.
            self.external_source = (
                "modbus" if self.external_source is None
                else normalize_external_source(self.external_source)
            )

            if self.external_trigger_mode != "explicit":
                self.external_input = None
                self.external_request_input = None
            else:
                raw_input = self.external_input
                if raw_input is None:
                    raw_input = self.external_request_input
                input_value = normalize_external_input(self.external_source, raw_input)
                self.external_input = input_value
                self.external_request_input = input_value

        if self.trigger_interval_ms is not None:
            try:
                self.trigger_interval_ms = int(self.trigger_interval_ms)
            except Exception:
                self.trigger_interval_ms = None
        if self.trigger_mode != "timed":
            self.trigger_interval_ms = None

        if self.trigger_gap_ms is not None:
            try:
                self.trigger_gap_ms = float(self.trigger_gap_ms)
            except Exception:
                self.trigger_gap_ms = None
        if self.trigger_gap_ms is not None and self.trigger_gap_ms <= 0:
            self.trigger_gap_ms = None

        try:
            rotation = int(self.image_rotation)
        except Exception:
            rotation = 0
        if rotation not in {0, 90, 180, 270}:
            rotation = 0
        self.image_rotation = rotation

        self.branch_enabled = bool(self.branch_enabled)
        normalized_targets: dict[str, str] = {}
        for key, value in dict(self.branch_targets or {}).items():
            status = str(key or "").strip().lower()
            if status not in {"ok", "warn", "nok"}:
                continue
            target_view = str(value or "").strip()
            if target_view:
                normalized_targets[status] = target_view
        self.branch_targets = normalized_targets

        branch_default = str(self.branch_default_view_id or "").strip()
        self.branch_default_view_id = branch_default or None

        converted: List[Tool] = []
        for tool in self.tools:
            if isinstance(tool, Tool):
                converted.append(tool.copy())
            else:
                converted.append(Tool.from_dict(tool))
        if self.id:
            for tool in converted:
                if not tool.view_id:
                    tool.view_id = self.id
        self.tools = converted

    def to_dict(self) -> Dict[str, Any]:
        if isinstance(self.camera_profile, ViewCameraProfile):
            camera_profile: Optional[Dict[str, Any]] | str = self.camera_profile.to_dict()
        else:
            camera_profile = self.camera_profile
        return {
            "id": self.id,
            "name": self.name,
            "golden_path": self.golden_path,
            "frame_source_view_id": self.frame_source_view_id,
            "camera_profile": camera_profile,
            "settle_ms": self.settle_ms,
            "flash_delay_ms": int(self.flash_delay_ms),
            "flash_pulse_ms": int(self.flash_pulse_ms),
            "trigger_mode": self.trigger_mode,
            "external_trigger_mode": self.external_trigger_mode,
            "external_source": self.external_source,
            "external_input": self.external_input,
            # Keep the old key during the compatibility window for older builds.
            "external_request_input": self.external_request_input,
            "trigger_interval_ms": self.trigger_interval_ms,
            "trigger_gap_ms": self.trigger_gap_ms,
            "image_rotation": self.image_rotation,
            "tools": [tool.to_dict() for tool in self.tools],
            "branch_enabled": self.branch_enabled,
            "branch_targets": dict(self.branch_targets),
            "branch_default_view_id": self.branch_default_view_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | "RecipeView") -> "RecipeView":
        if isinstance(data, RecipeView):
            return data.copy()
        if not isinstance(data, dict):
            raise TypeError("RecipeView.from_dict expects a dict or RecipeView instance")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            golden_path=data.get("golden_path", "golden.png"),
            frame_source_view_id=data.get("frame_source_view_id"),
            camera_profile=data.get("camera_profile"),
            settle_ms=data.get("settle_ms"),
            flash_delay_ms=data.get("flash_delay_ms", 0),
            flash_pulse_ms=data.get("flash_pulse_ms", 200),
            trigger_mode=data.get("trigger_mode", "timed"),
            external_trigger_mode=data.get("external_trigger_mode"),
            external_source=data.get("external_source"),
            external_input=data.get(
                "external_input",
                data.get(
                    "external_input_index",
                    data.get("external_request_input", data.get("modbus_input_index", data.get("modbus_input"))),
                ),
            ),
            trigger_interval_ms=data.get("trigger_interval_ms"),
            trigger_gap_ms=data.get("trigger_gap_ms"),
            image_rotation=data.get("image_rotation", 0),
            tools=data.get("tools", []),
            branch_enabled=bool(data.get("branch_enabled", False)),
            branch_targets=data.get("branch_targets", {}),
            branch_default_view_id=data.get("branch_default_view_id"),
        )

    def copy(self) -> "RecipeView":
        camera_profile = self.camera_profile
        if isinstance(camera_profile, ViewCameraProfile):
            camera_profile = camera_profile.copy()
        return RecipeView(
            id=self.id,
            name=self.name,
            golden_path=self.golden_path,
            frame_source_view_id=self.frame_source_view_id,
            camera_profile=camera_profile,
            settle_ms=self.settle_ms,
            flash_delay_ms=self.flash_delay_ms,
            flash_pulse_ms=self.flash_pulse_ms,
            trigger_mode=self.trigger_mode,
            external_trigger_mode=self.external_trigger_mode,
            external_source=self.external_source,
            external_input=self.external_input,
            external_request_input=self.external_request_input,
            trigger_interval_ms=self.trigger_interval_ms,
            trigger_gap_ms=self.trigger_gap_ms,
            image_rotation=self.image_rotation,
            tools=[tool.copy() for tool in self.tools],
            branch_enabled=self.branch_enabled,
            branch_targets=dict(self.branch_targets),
            branch_default_view_id=self.branch_default_view_id,
        )

    def set_tools(self, tools: Sequence[Tool]) -> None:
        converted = [tool.copy() for tool in tools]
        for tool in converted:
            tool.view_id = self.id
        self.tools = converted


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
    views: List[RecipeView] = field(default_factory=list)
    aggregation: RecipeAggregation = field(default_factory=RecipeAggregation)
    on_locator_failure: Literal["fail", "continue_without_alignment"] = (
        "continue_without_alignment"
    )
    export_artifacts: bool = False
    logging_enabled: bool = True

    def __post_init__(self) -> None:
        self.pose_enabled = bool(self.pose_enabled)
        self.regions = [dict(r) for r in self.regions]
        converted_tools: List[Tool] = []
        for tool in self.tools:
            if isinstance(tool, Tool):
                converted_tools.append(tool.copy())
            else:
                converted_tools.append(Tool.from_dict(tool))
        converted_tools.sort(key=lambda t: t.order)
        self.tools = converted_tools

        converted_views: List[RecipeView] = []
        for view in self.views:
            if isinstance(view, RecipeView):
                converted_views.append(view.copy())
            else:
                converted_views.append(RecipeView.from_dict(view))

        if not converted_views:
            default_view = RecipeView(
                id="view_1",
                name="View 1",
                golden_path="golden.png",
                tools=[tool.copy() for tool in self.tools],
            )
            converted_views = [default_view]
        else:
            for idx, view in enumerate(converted_views, start=1):
                if not view.id:
                    view.id = f"view_{idx}"
                if not view.name:
                    view.name = f"View {idx}"
                for tool in view.tools:
                    if not tool.view_id:
                        tool.view_id = view.id

        self.views = converted_views
        self.aggregation = RecipeAggregation.from_obj(self.aggregation)
        self._sync_tools_from_views()
        self.logging_enabled = bool(self.logging_enabled)

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
            "views": [view.to_dict() for view in self.views],
            "aggregation": self.aggregation.to_dict(),
            "on_locator_failure": self.on_locator_failure,
            "export_artifacts": bool(self.export_artifacts),
            "logging_enabled": bool(self.logging_enabled),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "RecipeV2":
        if not data:
            return cls()
        return cls(
            pose_enabled=data.get("pose_enabled", True),
            regions=data.get("regions", []),
            tools=data.get("tools", []),
            views=data.get("views", []),
            aggregation=data.get("aggregation"),
            on_locator_failure=data.get(
                "on_locator_failure", "continue_without_alignment"
            ),
            export_artifacts=bool(data.get("export_artifacts", False)),
            logging_enabled=bool(data.get("logging_enabled", True)),
        )

    @classmethod
    def from_recipe_data(cls, recipe: RecipeData) -> "RecipeV2":
        return cls(
            pose_enabled=recipe.pose_enabled,
            regions=recipe.regions,
            tools=[],
            views=[
                RecipeView(
                    id="view_1",
                    name="View 1",
                    golden_path="golden.png",
                    tools=[],
                )
            ],
            aggregation=RecipeAggregation(),
            on_locator_failure="continue_without_alignment",
            export_artifacts=False,
            logging_enabled=True,
        )

    def copy(self) -> "RecipeV2":
        return RecipeV2(
            pose_enabled=self.pose_enabled,
            regions=[deepcopy(r) for r in self.regions],
            tools=[tool.copy() for tool in self.tools],
            views=[view.copy() for view in self.views],
            aggregation=self.aggregation.copy(),
            on_locator_failure=self.on_locator_failure,
            export_artifacts=self.export_artifacts,
            logging_enabled=self.logging_enabled,
        )

    def with_tools(self, tools: Sequence[Tool]) -> "RecipeV2":
        new_recipe = self.copy()
        if new_recipe.views:
            new_recipe.views[0].set_tools(tools)
            new_recipe._sync_tools_from_views()
        else:
            new_recipe.tools = [tool.copy() for tool in tools]
        return new_recipe

    def iter_tools(self, view_id: Optional[str] = None) -> Iterable[Tool]:
        if view_id is None:
            return tuple(tool.copy() for tool in self.tools)
        view = self.get_view(view_id)
        return tuple(tool.copy() for tool in view.tools)

    def get_view(self, view_id: Optional[str] = None) -> RecipeView:
        if not self.views:
            raise ValueError("Recipe has no views configured")
        if view_id is None:
            return self.views[0]
        for view in self.views:
            if view.id == view_id:
                return view
        raise KeyError(f"View '{view_id}' not found in recipe")

    def _sync_tools_from_views(self) -> None:
        aggregated: List[Tool] = []
        base_index = 0
        for view in self.views:
            ordered_tools = sorted(view.tools, key=lambda t: (t.order, t.name))
            normalized_view_tools: List[Tool] = []
            for local_index, tool in enumerate(ordered_tools):
                tool_copy = tool.copy()
                tool_copy.order = local_index
                tool_copy.view_id = view.id
                normalized_view_tools.append(tool_copy)
                aggregated_tool = tool_copy.copy()
                aggregated_tool.order = base_index + local_index
                aggregated.append(aggregated_tool)
            view.tools = normalized_view_tools
            base_index += len(normalized_view_tools)
        self.tools = aggregated
        self.aggregation.align_with_views(self.views)

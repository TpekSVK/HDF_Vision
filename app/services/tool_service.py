# app/services/tool_service.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import math
import time

import imageio.v3 as iio
import numpy as np

from app.services.compare_service import analyze
from app.utils import imaging
from app.models.schema import (
    RecipeData,
    RecipeV2,
    Tool,
    ToolMask,
    ToolParams,
    ToolRoi,
    ToolThresholds,
)


@dataclass(frozen=True)
class ToolMeta:
    """Metadata describing capabilities and defaults for a tool type."""

    display_name: str
    description: str
    supports_roi: bool
    supports_ignore_mask: bool
    default_params: Dict[str, Any] = field(default_factory=dict)
    default_thresholds: Dict[str, Any] = field(default_factory=dict)
    category: str = "General"

    def copy_defaults(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Return deep copies of the default params and thresholds."""

        params = self._extract_defaults(self.default_params)
        thresholds = self._extract_defaults(self.default_thresholds)
        return params, thresholds

    @staticmethod
    def _extract_defaults(definitions: Dict[str, Any]) -> Dict[str, Any]:
        """Extract plain default values from a definition dictionary."""

        defaults: Dict[str, Any] = {}
        for key, spec in definitions.items():
            if isinstance(spec, dict):
                value = deepcopy(spec.get("default"))
            else:
                value = deepcopy(spec)
            defaults[key] = value
        return defaults


@dataclass(frozen=True)
class ToolRunResult:
    """Normalized result information returned by tool runners."""

    tool_id: str
    type: str
    status: Literal["ok", "warn", "nok"]
    metrics: Dict[str, Any] = field(default_factory=dict)
    preview: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "tool_id": self.tool_id,
            "type": self.type,
            "status": self.status,
            "metrics": dict(self.metrics),
        }
        if self.preview is not None:
            data["preview"] = self.preview
        return data


class ToolRegistry:
    """Central registry of available tool types and their metadata."""

    _TOOLS: Dict[str, ToolMeta] = {
        "ssim": ToolMeta(
            display_name="SSIM",
            description="Porovnanie štrukturálnej podobnosti v ROI.",
            supports_roi=True,
            supports_ignore_mask=True,
            default_params={},
            default_thresholds={
                "ssim_min": {
                    "type": "float",
                    "label": "ssim_min",
                    "default": 0.92,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "required": True,
                    "precision": 3,
                    "description": "Minimálna povolená hodnota štrukturálnej podobnosti (SSIM).",
                },
            },
            category="Similarity",
        ),
        "template_match": ToolMeta(
            display_name="Template Match",
            description="Lokalizácia objektu pomocou template matching.",
            supports_roi=True,
            supports_ignore_mask=False,
            default_params={
                "coarse_cap": {
                    "type": "int",
                    "label": "coarse_cap",
                    "default": 600,
                    "min": 64,
                    "max": 4096,
                    "step": 16,
                    "description": "Maximálna veľkosť hrubého search okna (px).",
                    "required": True,
                },
            },
            default_thresholds={
                "tm_enable": {
                    "type": "bool",
                    "label": "enable_template_match",
                    "default": True,
                    "description": "Povoliť template matching pred jemným zarovnaním.",
                },
                "tm_margin": {
                    "type": "int",
                    "label": "search_margin",
                    "default": 200,
                    "min": 0,
                    "max": 2000,
                    "step": 10,
                    "description": "Veľkosť search oblasti okolo očakávanej pozície (px).",
                },
                "tm_min_corr": {
                    "type": "float",
                    "label": "threshold_corr",
                    "default": 0.55,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "precision": 3,
                    "description": "Minimálna korelácia potrebná na úspešné nájdenie šablóny.",
                    "required": True,
                },
            },
            category="Locator",
        ),
        "locator.template_match": ToolMeta(
            display_name="Locator (Template Match)",
            description="Vyhľadávanie šablóny s podporou search a template ROI.",
            supports_roi=True,
            supports_ignore_mask=False,
            default_params={
                "use_golden_crop": {
                    "type": "bool",
                    "label": "use_golden_crop",
                    "default": True,
                    "description": "Použiť golden snapshot ako šablónu bez manuálneho výrezu.",
                },
                "coarse_to_fine": {
                    "type": "bool",
                    "label": "coarse_to_fine",
                    "default": True,
                    "description": "Povoliť dvojfázové hľadanie od hrubého po jemné zarovnanie.",
                },
                "coarse_cap": {
                    "type": "int",
                    "label": "coarse_cap",
                    "default": 600,
                    "min": 64,
                    "max": 4096,
                    "step": 16,
                    "description": "Maximálna veľkosť hrubého search okna (px).",
                },
                "apply_alignment": {
                    "type": "bool",
                    "label": "apply_alignment",
                    "default": True,
                    "description": "Aplikovať výsledné posunutie na nasledujúce nástroje.",
                },
                "template_roi": {
                    "type": "roi",
                    "label": "template_roi",
                    "default": None,
                    "description": "Manuálne definovaný template výrez na golden obrázku.",
                },
            },
            default_thresholds={
                "threshold_corr": {
                    "type": "float",
                    "label": "threshold_corr",
                    "default": 0.55,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "precision": 3,
                    "description": "Minimálna korelácia potrebná na úspešné zarovnanie.",
                },
            },
            category="Locator",
        ),
        "absdiff": ToolMeta(
            display_name="Abs Diff",
            description="Porovnanie absolútnych rozdielov s blob analýzou.",
            supports_roi=True,
            supports_ignore_mask=True,
            default_params={},
            default_thresholds={
                "diff_thresh": {
                    "type": "int",
                    "label": "diff_thresh",
                    "default": 15,
                    "min": 0,
                    "max": 255,
                    "step": 1,
                    "description": "Minimálna intenzita rozdielu pre detekciu pixelu (0–255).",
                },
                "min_blob_area": {
                    "type": "int",
                    "label": "min_blob_area",
                    "default": 20,
                    "min": 0,
                    "max": 1_000_000,
                    "step": 1,
                    "description": "Minimálna plocha jedného blobu (px²).",
                },
                "max_total_area": {
                    "type": "int",
                    "label": "max_total_area",
                    "default": 2000,
                    "min": 0,
                    "max": 10_000_000,
                    "step": 1,
                    "description": "Maximálna kumulovaná plocha všetkých blobov (px²).",
                },
                "max_blob_count": {
                    "type": "int",
                    "label": "max_blob_count",
                    "default": 10,
                    "min": 0,
                    "max": 10_000,
                    "step": 1,
                    "description": "Maximálny počet blobov povolený v ROI.",
                },
            },
            category="Inspection",
        ),
    }

    _SUPPORTED_FIELD_TYPES = {"int", "float", "bool", "enum", "roi"}

    @classmethod
    def list_tool_types(cls) -> List[str]:
        """Return available tool type identifiers."""

        return sorted(cls._TOOLS.keys())

    @classmethod
    def get_tool_meta(cls, tool_type: str) -> Optional[ToolMeta]:
        """Return metadata for a tool type if registered."""

        return cls._TOOLS.get(tool_type)

    @classmethod
    def get_tool_schema(cls, tool_type: str) -> Dict[str, Dict[str, Any]]:
        """Return normalized parameter/threshold definitions for the tool."""

        meta = cls.get_tool_meta(tool_type)
        if meta is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")
        params = cls._normalize_field_definitions(meta.default_params)
        thresholds = cls._normalize_field_definitions(meta.default_thresholds)
        return {"params": params, "thresholds": thresholds}

    @classmethod
    def _normalize_field_definitions(cls, definitions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        normalized: Dict[str, Dict[str, Any]] = {}
        for name, raw in (definitions or {}).items():
            if isinstance(raw, dict):
                spec = dict(raw)
            else:
                spec = {"default": raw}
            spec.setdefault("label", name)
            type_name = spec.get("type")
            if type_name is None:
                type_name = cls._infer_field_type(spec.get("default"))
            else:
                type_name = str(type_name).lower()
            if type_name == "enum" and "choices" not in spec and "options" in spec:
                spec["choices"] = spec.get("options")
            if type_name not in cls._SUPPORTED_FIELD_TYPES:
                continue
            if type_name == "enum":
                normalized_choices: list[tuple[Any, str]] = []
                for choice in spec.get("choices", []) or []:
                    if isinstance(choice, dict):
                        value = choice.get("value")
                        label = choice.get("label", str(value))
                    elif isinstance(choice, (list, tuple)) and choice:
                        value = choice[0]
                        label = choice[1] if len(choice) > 1 else str(choice[0])
                    else:
                        value = choice
                        label = str(choice)
                    normalized_choices.append((value, label))
                spec["choices"] = normalized_choices
            spec["type"] = type_name
            normalized[name] = spec
        return normalized

    @staticmethod
    def _infer_field_type(value: Any) -> str | None:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int) and not isinstance(value, bool):
            return "int"
        if isinstance(value, float):
            return "float"
        return None

    @classmethod
    def make_default_tool(cls, tool_type: str, name: Optional[str] = None) -> Tool:
        """Instantiate a Tool with default metadata for the given type."""

        meta = cls.get_tool_meta(tool_type)
        if meta is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")

        params, thresholds = meta.copy_defaults()
        tool_name = name if name is not None else meta.display_name

        ignore_mask = ToolMask() if meta.supports_ignore_mask else ToolMask(None)

        return Tool(
            type=tool_type,
            name=tool_name,
            enabled=True,
            order=0,
            roi=ToolRoi(),
            ignore_mask=ignore_mask,
            params=ToolParams(params),
            thresholds=ToolThresholds(thresholds),
        )


def _coerce_bool(value: Any) -> tuple[Optional[bool], Optional[str]]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        return bool(value), None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True, None
        if text in {"0", "false", "no", "n", "off"}:
            return False, None
        return None, "Value must be 'true' or 'false'."
    return None, "Invalid boolean value."


def _coerce_numeric(value: Any, *, number_type: str) -> tuple[Optional[float], Optional[str]]:
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "Boolean value is not allowed."
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None, None
        try:
            if number_type == "int":
                return float(int(text, 10)), None
            return float(text)
        except (ValueError, TypeError):
            return None, "Value must be a number."
    return None, "Value must be a number."


def _apply_numeric_constraints(
    value: float,
    spec: Dict[str, Any],
    *,
    number_type: str,
) -> tuple[Any, list[str]]:
    errors: list[str] = []
    if math.isnan(value) or math.isinf(value):
        return None, ["Value must be a finite number."]

    min_val = spec.get("min")
    max_val = spec.get("max")

    if min_val is not None and value < float(min_val):
        errors.append(f"Value must be ≥ {min_val}.")
    if max_val is not None and value > float(max_val):
        errors.append(f"Value must be ≤ {max_val}.")

    clamped = value
    if min_val is not None:
        clamped = max(clamped, float(min_val))
    if max_val is not None:
        clamped = min(clamped, float(max_val))

    if number_type == "int":
        rounded = int(round(clamped))
        return rounded, errors

    precision = spec.get("precision")
    if precision is None:
        precision = spec.get("decimals")
    if isinstance(precision, int) and precision >= 0:
        clamped = round(clamped, precision)

    return float(clamped), errors


def _normalize_field_value(value: Any, spec: Dict[str, Any]) -> tuple[Any, list[str]]:
    errors: list[str] = []
    required = bool(spec.get("required"))
    field_type = spec.get("type")

    if value is None or value == "":
        if required:
            errors.append("This field is required.")
            return None, errors
        default = spec.get("default")
        return default, errors

    if field_type == "bool":
        coerced, err = _coerce_bool(value)
        if err:
            errors.append(err)
            return None, errors
        return coerced, errors

    if field_type == "enum":
        valid_choices = {choice[0] for choice in spec.get("choices", []) or []}
        if value not in valid_choices:
            errors.append("Select one of the available options.")
            return None, errors
        return value, errors

    if field_type in {"int", "float"}:
        coerced, err = _coerce_numeric(value, number_type=field_type)
        if err:
            errors.append(err)
            return None, errors
        if coerced is None:
            if required:
                errors.append("This field is required.")
            return None, errors
        normalized, range_errors = _apply_numeric_constraints(
            coerced, spec, number_type=field_type
        )
        errors.extend(range_errors)
        return normalized, errors

    return value, errors


def validate_tool_params(
    type_id: str,
    params: Optional[Dict[str, Any]],
    thresholds: Optional[Dict[str, Any]],
) -> tuple[bool, Dict[str, Dict[str, list[str]]], Dict[str, Dict[str, Any]]]:
    """Validate and normalize tool parameters against the registry schema.

    Returns
    -------
    ok:
        ``True`` when all values satisfy the schema.
    errors:
        Mapping containing error messages per parameter/threshold key.
    normalized:
        Mapping of normalized values respecting types, precision and ranges.
    """

    schema = ToolRegistry.get_tool_schema(type_id)
    param_specs = schema.get("params", {}) or {}
    threshold_specs = schema.get("thresholds", {}) or {}

    params_input = dict(params or {})
    thresholds_input = dict(thresholds or {})

    normalized_params = dict(params_input)
    normalized_thresholds = dict(thresholds_input)
    param_errors: Dict[str, list[str]] = {}
    threshold_errors: Dict[str, list[str]] = {}

    for name, spec in param_specs.items():
        raw_value = params_input.get(name)
        value, errors = _normalize_field_value(raw_value, spec)
        if errors:
            param_errors[name] = errors
        if value is None and name in normalized_params:
            normalized_params.pop(name, None)
        elif value is not None:
            normalized_params[name] = value

    for name, spec in threshold_specs.items():
        raw_value = thresholds_input.get(name)
        value, errors = _normalize_field_value(raw_value, spec)
        if errors:
            threshold_errors[name] = errors
        if value is None and name in normalized_thresholds:
            normalized_thresholds.pop(name, None)
        elif value is not None:
            normalized_thresholds[name] = value

    ok = not param_errors and not threshold_errors
    errors = {"params": param_errors, "thresholds": threshold_errors}
    normalized = {"params": normalized_params, "thresholds": normalized_thresholds}
    return ok, errors, normalized


DEFAULT_THRESHOLDS = {
    "ssim_min": 0.92,
    "diff_thresh": 15,
    "min_blob_area": 20,
    "max_total_area": 2000,
    "max_blob_count": 10,
}

class ToolService:
    def __init__(self, base_dir="/data"):
        self.base = Path(base_dir)
        self.recipe = "default"
        self.golden = None            # np.ndarray uint8
        self.regions = None           # list[dict]
        self.thresholds = DEFAULT_THRESHOLDS.copy()
        self.pose_enabled = True

    # ------------------------------------------------------------------
    # Tool registry API
    # ------------------------------------------------------------------
    def list_tool_types(self) -> List[str]:
        """Return registered tool types."""

        return ToolRegistry.list_tool_types()

    def get_tool_meta(self, tool_type: str) -> ToolMeta:
        """Retrieve metadata for a given tool type."""

        meta = ToolRegistry.get_tool_meta(tool_type)
        if meta is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")
        return meta

    def get_tool_schema(self, tool_type: str) -> Dict[str, Dict[str, Any]]:
        """Expose registry schema information for UI consumption."""

        return ToolRegistry.get_tool_schema(tool_type)

    def make_default_tool(self, tool_type: str, name: Optional[str] = None) -> Tool:
        """Create a ``Tool`` instance with registry defaults."""

        return ToolRegistry.make_default_tool(tool_type, name=name)

    def load_recipe(self, name: str):
        self.recipe = name
        rdir = self.base / "recipes" / name
        gfp = rdir / "golden.png"
        rfp = rdir / "regions.json"
        if not gfp.exists() or not rfp.exists():
            raise FileNotFoundError(f"Recept {name} nie je kompletný (chýba golden alebo regions.json)")

        g = iio.imread(gfp)
        if g.ndim == 3:
            g = g[:, :, 0]
        if g.dtype != np.uint8:
            # ak by bol 16-bit, znormalizuj na uint8
            g = (g.astype(np.float32) * (255.0 / g.max())).astype(np.uint8)
        with open(rfp, "r", encoding="utf-8") as f:
            data = json.load(f)
        recipe = RecipeData.from_dict(data)

        self.golden = g
        self.regions = recipe.regions
        self.pose_enabled = recipe.pose_enabled

        # voliteľne načítaj thresholds.json ak existuje
        tfp = rdir / "thresholds.json"
        if tfp.exists():
            with open(tfp, "r", encoding="utf-8") as f:
                th = json.load(f)
            self.thresholds.update(th)

    def save_thresholds(self):
        rdir = self.base / "recipes" / self.recipe
        rdir.mkdir(parents=True, exist_ok=True)
        with open(rdir / "thresholds.json", "w", encoding="utf-8") as f:
            json.dump(self.thresholds, f, ensure_ascii=False, indent=2)

    def evaluate(self, frame_u8):
        if self.golden is None or self.regions is None:
            raise RuntimeError("Recept nie je načítaný.")
        return analyze(
            self.golden,
            self.regions,
            frame_u8,
            self.thresholds,
            pose_enabled=self.pose_enabled,
        )


@dataclass(slots=True)
class ToolRunnerContext:
    """Context shared across tool execution within the pipeline."""

    frame: np.ndarray
    frame_aligned: np.ndarray | None = None
    T_total: np.ndarray | None = None
    frame_is_aligned: bool = False


def compose_affine(
    T_total: np.ndarray | None, T_new: np.ndarray | None
) -> np.ndarray:
    """Left-compose 2×3 affine transforms.

    The resulting matrix corresponds to applying ``T_total`` first and then
    ``T_new`` (``T_new ∘ T_total``). ``None`` inputs are treated as identity
    transforms.
    """

    def _to_homogeneous(mat: np.ndarray | None) -> np.ndarray:
        if mat is None:
            return np.eye(3, dtype=np.float32)

        arr = np.asarray(mat, dtype=np.float32)
        if arr.shape != (2, 3):  # pragma: no cover - defensive programming
            raise ValueError("Affine transform must have shape (2, 3)")

        homo = np.eye(3, dtype=np.float32)
        homo[:2, :3] = arr
        return homo

    M_total = _to_homogeneous(T_total)
    M_new = _to_homogeneous(T_new)
    composed = M_new @ M_total
    return composed[:2, :3]


def _validate_roi(tool: Tool, meta: ToolMeta) -> None:
    roi_rect = tool.roi.rect()
    if roi_rect is None:
        return
    if not meta.supports_roi:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ROI but one was provided"
        )
    x, y, w, h = roi_rect
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid ROI dimensions for tool '{tool.name}'")


def _validate_ignore_mask(tool: Tool, meta: ToolMeta) -> None:
    mask = tool.ignore_mask.value
    if mask is None:
        return
    if not meta.supports_ignore_mask:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ignore mask"
        )
    if mask.ndim != 2:
        raise ValueError(f"Ignore mask for tool '{tool.name}' must be 2D")


def _validate_params(tool: Tool) -> None:
    params = tool.params.values
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError(f"Params for tool '{tool.name}' must be a dictionary")


def _ensure_locator_first(tools: Sequence[Tool]) -> List[Tool]:
    """Ensure locator tools are positioned before other tools."""

    locators = [tool for tool in tools if tool.type.startswith("locator.")]
    analyzers = [tool for tool in tools if not tool.type.startswith("locator.")]
    return list(locators + analyzers)


def run_pipeline(
    recipe: RecipeV2, golden: np.ndarray, frame: np.ndarray
) -> Tuple[ToolRunnerContext, List[Dict[str, Any]], List[ToolRunResult]]:
    """Iterate through tool pipeline and update shared context.

    Returns the updated context, diagnostic payloads and normalized
    :class:`ToolRunResult` entries in execution order.
    """

    diagnostics: List[Dict[str, Any]] = []
    results: List[ToolRunResult] = []
    context = ToolRunnerContext(
        frame=frame,
        frame_aligned=frame,
        T_total=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        frame_is_aligned=False,
    )

    tools: Sequence[Tool] = sorted(recipe.tools, key=lambda t: t.order)
    tools = _ensure_locator_first(tools)

    for tool in tools:
        meta = ToolRegistry.get_tool_meta(tool.type)
        if meta is None:
            raise ValueError(f"Tool type '{tool.type}' is not registered")

        _validate_roi(tool, meta)
        _validate_ignore_mask(tool, meta)
        _validate_params(tool)

        tool_id = tool.name or f"tool_{tool.order}"
        diag_entry: Dict[str, Any] = {
            "tool_id": tool_id,
            "type": tool.type,
            "status": "disabled" if not tool.enabled else "skipped",
        }

        if not tool.enabled:
            diagnostics.append(diag_entry)
            continue

        if tool.type == "locator.template_match":
            params_dict = dict(tool.params.values or {})

            frame_for_locator = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )

            tool_result, locator_diag = run_locator_template_match(
                golden,
                frame_for_locator,
                tool.params,
                tool.thresholds,
                tool.roi,
                tool_id=tool_id,
            )

            diag_entry.update(locator_diag)
            diag_entry["status"] = locator_diag.get("status", tool_result.status)

            results.append(tool_result)

            T_new = locator_diag.get("T")
            context.T_total = compose_affine(context.T_total, T_new)

            apply_alignment = bool(params_dict.get("apply_alignment", True))
            if apply_alignment:
                source = (
                    context.frame_aligned
                    if context.frame_aligned is not None
                    else context.frame
                )
                dx = float(locator_diag.get("dx", 0.0))
                dy = float(locator_diag.get("dy", 0.0))
                context.frame_aligned = imaging.warp_by_translation_u8(source, -dx, -dy)
                context.frame_is_aligned = True
            else:
                context.frame_aligned = context.frame
                context.frame_is_aligned = False

        elif tool.type == "ssim":
            frame_for_ssim = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )

            tool_result, ssim_diag = run_ssim_tool(
                golden,
                frame_for_ssim,
                tool.thresholds,
                tool.roi,
                frame_original=context.frame,
                T_total=context.T_total,
                frame_is_aligned=context.frame_is_aligned,
                tool_id=tool_id,
            )

            diag_entry.update(ssim_diag)
            diag_entry["status"] = tool_result.status
            results.append(tool_result)

        else:
            diag_entry["status"] = "skipped"

        diagnostics.append(diag_entry)

    return context, diagnostics, results


def run_tool_isolated(
    tool_type: str,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    context: ToolRunnerContext,
    golden: np.ndarray,
    frame: np.ndarray | None,
) -> ToolRunResult:
    """Execute a single tool using an existing runner context."""

    if context is None:
        raise ValueError("Context is required for isolated tool run")
    if golden is None:
        raise ValueError("Golden image is required for isolated tool run")

    frame_array: np.ndarray | None = None
    if frame is not None:
        frame_array = np.asarray(frame)

    if context.frame is None:
        if frame_array is None:
            raise ValueError("Frame image is required for isolated tool run")
        context.frame = frame_array
    else:
        context.frame = np.asarray(context.frame)
        frame_array = context.frame

    if context.frame_aligned is None:
        context.frame_aligned = context.frame
    if context.T_total is None:
        context.T_total = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    if context.frame_is_aligned is None:
        context.frame_is_aligned = False

    params_dict = dict(getattr(params, "values", params) or {})
    thresholds_dict = dict(getattr(thresholds, "values", thresholds) or {})
    roi_value = params_dict.pop("__roi__", None)
    if roi_value is None:
        roi_value = thresholds_dict.pop("__roi__", None)
    roi = ToolRoi.from_obj(roi_value) if roi_value is not None else ToolRoi()

    if tool_type == "locator.template_match":
        frame_for_tool = context.frame_aligned if context.frame_aligned is not None else context.frame
        if frame_for_tool is None:
            raise ValueError("Frame not available for locator tool")

        params_obj = ToolParams(params_dict)
        thresholds_obj = ToolThresholds(thresholds_dict)
        result, diagnostics = run_locator_template_match(
            golden,
            frame_for_tool,
            params_obj,
            thresholds_obj,
            roi,
            tool_id=tool_type,
        )

        T_new = diagnostics.get("T")
        context.T_total = compose_affine(context.T_total, T_new)

        apply_alignment = bool(params_dict.get("apply_alignment", True))
        if apply_alignment:
            source = context.frame_aligned if context.frame_aligned is not None else context.frame
            if source is None:
                raise ValueError("Aligned frame source not available")
            dx = float(diagnostics.get("dx", 0.0))
            dy = float(diagnostics.get("dy", 0.0))
            context.frame_aligned = imaging.warp_by_translation_u8(source, -dx, -dy)
            context.frame_is_aligned = True
        else:
            context.frame_aligned = context.frame
            context.frame_is_aligned = False

        return result

    if tool_type == "ssim":
        frame_for_tool = context.frame_aligned if context.frame_aligned is not None else context.frame
        if frame_for_tool is None:
            raise ValueError("Frame not available for SSIM tool")

        thresholds_obj = ToolThresholds(thresholds_dict)
        result, _ = run_ssim_tool(
            golden,
            frame_for_tool,
            thresholds_obj,
            roi,
            frame_original=context.frame if context.frame is not None else frame_for_tool,
            T_total=context.T_total,
            frame_is_aligned=context.frame_is_aligned,
            tool_id=tool_type,
        )
        return result

    raise ValueError(f"Unsupported tool type '{tool_type}' for isolated run")


def _rect_from_any(value: Any) -> Optional[Tuple[int, int, int, int]]:
    """Normalize various ROI representations to an (x, y, w, h) tuple."""

    if value is None:
        return None
    if isinstance(value, ToolRoi):
        return value.rect()
    if isinstance(value, dict):
        keys = ("x", "y", "w", "h")
        if all(k in value for k in keys):
            try:
                return (
                    int(round(float(value["x"]))),
                    int(round(float(value["y"]))),
                    int(round(float(value["w"]))),
                    int(round(float(value["h"]))),
                )
            except Exception:
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            x, y, w, h = value[:4]
            return (
                int(round(float(x))),
                int(round(float(y))),
                int(round(float(w))),
                int(round(float(h))),
            )
        except Exception:
            return None
    return None


def _clamp_rect(
    rect: Optional[Tuple[int, int, int, int]], width: int, height: int
) -> Optional[Tuple[int, int, int, int]]:
    """Clamp a rectangle to image bounds. Returns ``None`` if empty."""

    if rect is None:
        return (0, 0, width, height)

    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None

    x1 = max(0, min(width, x))
    y1 = max(0, min(height, y))
    x2 = max(0, min(width, x + w))
    y2 = max(0, min(height, y + h))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2 - x1, y2 - y1


def _ensure_gray_u8(image: np.ndarray) -> np.ndarray:
    """Convert an image to a 2D ``uint8`` array without copying when possible."""

    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    return arr


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _extract_translation_from_affine(T: np.ndarray | None) -> tuple[float, float]:
    """Return translation components from a 2×3 affine transform."""

    if T is None:
        return 0.0, 0.0

    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):  # pragma: no cover - defensive fallback
        return 0.0, 0.0

    return float(arr[0, 2]), float(arr[1, 2])


def run_locator_template_match(
    golden: np.ndarray,
    frame: np.ndarray,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    roi: Optional[ToolRoi | Dict[str, Any] | Sequence[int]],
    *,
    tool_id: str = "locator",
) -> Tuple[ToolRunResult, Dict[str, Any]]:
    """Run template matching locator core logic.

    Parameters
    ----------
    golden, frame:
        Golden reference and frame images. Only the first channel is used if
        images are multi-channel.
    params:
        Tool parameters or plain dictionary. Recognized keys are
        ``use_golden_crop`` (bool), ``template_roi`` (ROI descriptor) and
        ``coarse_cap`` (int).
    thresholds:
        Threshold dictionary or :class:`ToolThresholds`. Uses
        ``threshold_corr`` if present.
    roi:
        Search ROI descriptor applied on the frame.

    Returns
    -------
    tuple
        ``(ToolRunResult, diagnostics)`` pair. Diagnostics include
        ``dx``, ``dy``, ``corr`` and affine transform ``T``.
    """

    start_time = time.perf_counter()

    params_dict = (
        params.values if isinstance(params, ToolParams) else dict(params or {})
    )
    thresholds_dict = (
        thresholds.values
        if isinstance(thresholds, ToolThresholds)
        else dict(thresholds or {})
    )

    golden_u8 = _ensure_gray_u8(golden)
    frame_u8 = _ensure_gray_u8(frame)

    frame_h, frame_w = frame_u8.shape[:2]
    golden_h, golden_w = golden_u8.shape[:2]

    search_rect = _rect_from_any(roi)
    search_rect = _clamp_rect(search_rect, frame_w, frame_h)

    use_golden_crop = bool(params_dict.get("use_golden_crop", True))
    template_source = None
    if not use_golden_crop:
        template_source = _rect_from_any(params_dict.get("template_roi"))
    if template_source is None:
        template_source = _rect_from_any(search_rect)

    template_rect = _clamp_rect(template_source, golden_w, golden_h)

    coarse_cap = _safe_int(params_dict.get("coarse_cap", 600), 600)
    threshold_corr = _safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55)

    dx = 0.0
    dy = 0.0
    corr = 0.0
    status: Literal["ok", "warn", "nok"] = "warn"
    used = 0

    if template_rect is not None and search_rect is not None:
        tx, ty, tw, th = template_rect
        templ = golden_u8[ty : ty + th, tx : tx + tw]

        if templ.size > 0 and tw > 0 and th > 0:
            sx, sy, sw, sh = search_rect

            if sw >= tw and sh >= th:
                dx_rel, dy_rel, corr, used = imaging.match_template_u8(
                    frame_u8,
                    templ,
                    roi=(sx, sy, sw, sh),
                    search_margin=0,
                    coarse_cap=int(max(1, coarse_cap)),
                )

                dx = float((sx + dx_rel) - tx)
                dy = float((sy + dy_rel) - ty)

    if used == 0 and corr == 0.0:
        status = "warn"
    elif corr >= threshold_corr:
        status = "ok"
    else:
        status = "nok"

    T = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)

    metrics = {"dx": dx, "dy": dy, "corr": corr}
    diagnostics = {**metrics, "T": T, "status": status}

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    metrics["latency_ms"] = latency_ms
    diagnostics["latency_ms"] = latency_ms
    result = ToolRunResult(
        tool_id=tool_id,
        type="locator.template_match",
        status=status,
        metrics=metrics,
    )
    return result, diagnostics


def run_ssim_tool(
    golden: np.ndarray,
    frame: np.ndarray,
    thresholds: Dict[str, Any] | ToolThresholds,
    roi: Optional[ToolRoi | Dict[str, Any] | Sequence[int]],
    *,
    frame_original: np.ndarray,
    T_total: np.ndarray | None,
    frame_is_aligned: bool,
    tool_id: str = "ssim",
) -> Tuple[ToolRunResult, Dict[str, Any]]:
    """Compute SSIM within ROI, honoring optional locator alignment."""

    start_time = time.perf_counter()

    golden_u8 = _ensure_gray_u8(golden)
    frame_u8 = _ensure_gray_u8(frame)
    frame_orig_u8 = _ensure_gray_u8(frame_original)

    thresholds_dict = (
        thresholds.values
        if isinstance(thresholds, ToolThresholds)
        else dict(thresholds or {})
    )
    ssim_min = float(thresholds_dict.get("ssim_min", DEFAULT_THRESHOLDS.get("ssim_min", 0.92)))

    roi_rect = _rect_from_any(roi)
    gh, gw = golden_u8.shape[:2]
    roi_rect = _clamp_rect(roi_rect, gw, gh)
    if roi_rect is None:
        roi_rect = (0, 0, gw, gh)

    dx_total, dy_total = _extract_translation_from_affine(T_total)
    virtual_alignment = False
    if not frame_is_aligned and (abs(dx_total) > 1e-3 or abs(dy_total) > 1e-3):
        frame_u8 = imaging.warp_by_translation_u8(frame_orig_u8, -dx_total, -dy_total)
        virtual_alignment = True

    x, y, w, h = roi_rect
    golden_crop = golden_u8[y : y + h, x : x + w]
    frame_crop = frame_u8[y : y + h, x : x + w]

    ssim_val = float(imaging.ssim_u8(golden_crop, frame_crop))
    status: Literal["ok", "warn", "nok"] = "ok" if ssim_val >= ssim_min else "nok"

    metrics = {"ssim": round(ssim_val, 5)}
    diagnostics = {
        "ssim": ssim_val,
        "roi": {"x": x, "y": y, "w": w, "h": h},
        "virtual_alignment": virtual_alignment,
        "ssim_min": ssim_min,
        "dx_total": dx_total,
        "dy_total": dy_total,
    }

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    metrics["latency_ms"] = latency_ms
    diagnostics["latency_ms"] = latency_ms

    result = ToolRunResult(
        tool_id=tool_id,
        type="ssim",
        status=status,
        metrics=metrics,
    )
    return result, diagnostics


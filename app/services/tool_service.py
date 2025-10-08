from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Protocol, Sequence, Tuple

import numpy as np

from app.models.schema import (
    RecipeV2,
    Tool,
    ToolParams,
    ToolRoi,
    ToolThresholds,
    ToolDefinition,
    ToolDefinitionMeta,
    ToolDefinitionMetaSchema,
    ToolMetricSpec,
)
from app.services.compare_service import analyze
from app.services.tool_registry import registry, ToolRegistry
from app.utils import imaging


@dataclass(slots=True)
class ToolRunResult:
    """Normalized result returned by tools."""

    status: Literal["ok", "nok", "warn"]
    metrics: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    debug_artifacts: Optional[Dict[str, Any]] = None

    @staticmethod
    def make_default_tool(type_id: str, name: str | None = None) -> Tool:
        return registry.make_default_tool(type_id, name=name)

class ITool(Protocol):
    """Interface implemented by all concrete tools."""

    def prepare(self, context: "ToolRunnerContext") -> None:
        ...

    def run(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: "ToolRunnerContext",
    ) -> ToolRunResult:
        ...

    def teardown(self) -> None:
        ...


@dataclass(slots=True)
class ToolRunnerContext:
    frame: np.ndarray
    frame_aligned: np.ndarray | None = None
    T_total: np.ndarray | None = None
    frame_is_aligned: bool = False


@dataclass(frozen=True)
class ToolMetaView:
    display_name: str
    description: str
    supports_roi: bool
    supports_ignore_mask: bool
    category: str


class ToolRegistry:
    """Compatibility facade exposing registry helpers to the UI layer."""

    @staticmethod
    def list_tool_types() -> list[str]:
        return list(registry.list_tool_types())

    @staticmethod
    def get_tool_meta(type_id: str) -> ToolMetaView | None:
        definition = registry.get_definition(type_id)
        if definition is None:
            return None
        meta = definition.meta
        return ToolMetaView(
            display_name=definition.display_name,
            description=meta.description,
            supports_roi=meta.supports_roi,
            supports_ignore_mask=meta.supports_ignore_mask,
            category=meta.category,
        )

    @staticmethod
    def get_tool_definition(type_id: str) -> ToolDefinition | None:
        return registry.get_definition(type_id)

    @staticmethod
    def get_tool_schema(type_id: str) -> Dict[str, Dict[str, Any]]:
        return registry.get_schema(type_id)

    @staticmethod
    def make_default_tool(type_id: str, name: str | None = None) -> Tool:
        return registry.make_default_tool(type_id, name=name)


ToolMeta = ToolMetaView


# ---------------------------------------------------------------------------
# Backwards compatible ToolService facade
# ---------------------------------------------------------------------------

        if template_rect is not None and search_rect is not None:
            tx, ty, tw, th = template_rect
            sx, sy, sw, sh = search_rect
            templ = golden_u8[ty : ty + th, tx : tx + tw]
            if templ.size > 0 and sw >= tw and sh >= th:
                dx_rel, dy_rel, corr, used = imaging.match_template_u8(
                    frame_u8,
                    templ,
                    roi=(sx, sy, sw, sh),
                    search_margin=0,
                    coarse_cap=int(coarse_cap),
                )
                dx = float((sx + dx_rel) - tx)
                dy = float((sy + dy_rel) - ty)

        status: Literal["ok", "nok", "warn"]
        if used == 0:
            status = "warn"
        elif corr >= threshold_corr:
            status = "ok"
        else:
            status = "nok"


class ToolService:
    """Facade preserved for UI/services that expect the legacy API."""

    def __init__(self, base_dir: str = "/data"):
        self.base = Path(base_dir)
        self.recipe = "default"
        self.golden: np.ndarray | None = None
        self.regions: list[dict[str, Any]] | None = None
        self.thresholds: dict[str, Any] = dict(DEFAULT_THRESHOLDS)
        self.pose_enabled: bool = True

    # ------------------------------------------------------------------
    # Tool registry helpers
    # ------------------------------------------------------------------
    def list_tool_types(self) -> list[str]:
        return ToolRegistry.list_tool_types()

    def get_tool_meta(self, tool_type: str) -> ToolMetaView:
        meta = ToolRegistry.get_tool_meta(tool_type)
        if meta is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")
        return meta

    def get_tool_schema(self, tool_type: str) -> Dict[str, Dict[str, Any]]:
        return ToolRegistry.get_tool_schema(tool_type)

    def make_default_tool(self, tool_type: str, name: str | None = None) -> Tool:
        return ToolRegistry.make_default_tool(tool_type, name=name)

    # ------------------------------------------------------------------
    # Persistence helpers reused by UI
    # ------------------------------------------------------------------
    def load_recipe(self, name: str) -> None:
        self.recipe = name
        recipe_dir = self.base / "recipes" / name
        golden_path = recipe_dir / "golden.png"
        regions_path = recipe_dir / "regions.json"
        if not golden_path.exists() or not regions_path.exists():
            raise FileNotFoundError(
                f"Recept {name} nie je kompletný (chýba golden alebo regions.json)"
            )

        golden = iio.imread(golden_path)
        if golden.ndim == 3:
            golden = golden[:, :, 0]
        if golden.dtype != np.uint8:
            scale = 255.0 / float(golden.max() or 1)
            golden = (golden.astype(np.float32) * scale).astype(np.uint8)

        with open(regions_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        recipe = RecipeData.from_dict(data)

        self.golden = golden
        self.regions = recipe.regions
        self.pose_enabled = recipe.pose_enabled

        thresholds_path = recipe_dir / "thresholds.json"
        if thresholds_path.exists():
            with open(thresholds_path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            self.thresholds.update(stored)

    def save_thresholds(self) -> None:
        recipe_dir = self.base / "recipes" / self.recipe
        recipe_dir.mkdir(parents=True, exist_ok=True)
        with open(recipe_dir / "thresholds.json", "w", encoding="utf-8") as fh:
            json.dump(self.thresholds, fh, ensure_ascii=False, indent=2)

    def evaluate(self, frame_u8: np.ndarray) -> Any:
        if self.golden is None or self.regions is None:
            raise RuntimeError("Recept nie je načítaný.")
        return analyze(
            self.golden,
            self.regions,
            frame_u8,
            self.thresholds,
            pose_enabled=self.pose_enabled,
        )

_register_builtin_tools()

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ensure_gray_u8(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3 and arr.shape[2] == 3:
        return imaging.rgb_to_gray_u8(arr)
    if arr.dtype != np.uint8:
        arr = imaging.to_uint8(arr)
    return arr


def _rect_from_any(value: Any) -> Optional[Tuple[int, int, int, int]]:
    if value is None:
        return None
    if isinstance(value, ToolRoi):
        return value.rect()
    if isinstance(value, dict):
        try:
            return (
                int(round(float(value.get("x", 0)))),
                int(round(float(value.get("y", 0)))),
                int(round(float(value.get("w", 0)))),
                int(round(float(value.get("h", 0)))),
            )
        except Exception:
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


def _rect_to_dict(rect: Optional[Tuple[int, int, int, int]]) -> Optional[Dict[str, int]]:
    if rect is None:
        return None
    x, y, w, h = rect
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def _clamp_rect(
    rect: Optional[Tuple[int, int, int, int]],
    width: int,
    height: int,
) -> Optional[Tuple[int, int, int, int]]:
    if rect is None:
        return None
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None
    x = max(0, min(int(x), width))
    y = max(0, min(int(y), height))
    w = min(int(w), width - x)
    h = min(int(h), height - y)
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def compose_affine(
    T_total: np.ndarray | None,
    T_new: np.ndarray | None,
) -> np.ndarray:
    def _to_homogeneous(mat: np.ndarray | None) -> np.ndarray:
        if mat is None:
            return np.eye(3, dtype=np.float32)
        arr = np.asarray(mat, dtype=np.float32)
        if arr.shape != (2, 3):
            raise ValueError("Affine transform must have shape (2, 3)")
        homo = np.eye(3, dtype=np.float32)
        homo[:2, :3] = arr
        return homo

    ok = not param_errors and not threshold_errors
    errors = {"params": param_errors, "thresholds": threshold_errors}
    normalized = {"params": normalized_params, "thresholds": normalized_thresholds}
    return ok, errors, normalized


def _extract_translation_from_affine(T: np.ndarray | None) -> Tuple[float, float]:
    if T is None:
        return 0.0, 0.0
    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):
        return 0.0, 0.0
    return float(arr[0, 2]), float(arr[1, 2])


def run_locator_template_match(
    golden: np.ndarray,
    frame: np.ndarray,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    roi: Optional[ToolRoi | Dict[str, Any] | Sequence[int]],
    *,
    tool_type: str = "locator.template_match",
) -> Tuple[ToolRunResult, Dict[str, Any]]:
    """Compatibility wrapper used by UI/tests to run the locator in isolation."""

    context = ToolRunnerContext(frame=np.asarray(frame))

    params_obj = ToolParams.from_obj(params)
    thresholds_obj = ToolThresholds.from_obj(thresholds)

    roi_payload = ToolRoi.from_obj(roi).to_dict() if roi is not None else {}
    if roi_payload:
        params_obj.values["__roi__"] = roi_payload
    else:
        params_obj.values.pop("__roi__", None)

    result = run_tool_isolated(
        tool_type,
        golden=golden,
        context=context,
        params=params_obj,
        thresholds=thresholds_obj,
        frame=frame,
    )

    diagnostics = dict(result.debug_artifacts or {})
    if not diagnostics:
        diagnostics = dict(result.metrics)

    diagnostics.setdefault("dx", result.metrics.get("dx", 0.0))
    diagnostics.setdefault("dy", result.metrics.get("dy", 0.0))
    diagnostics.setdefault("corr", result.metrics.get("corr", 0.0))
    diagnostics.setdefault("latency_ms", result.latency_ms)
    diagnostics.setdefault("status", result.status)

    if "T" not in diagnostics:
        dx = float(diagnostics.get("dx", 0.0) or 0.0)
        dy = float(diagnostics.get("dy", 0.0) or 0.0)
        diagnostics["T"] = np.array(
            [[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32
        )

    return result, diagnostics


def _normalize_field_value(value: Any, spec: Dict[str, Any]) -> tuple[Any, list[str]]:
    errors: list[str] = []
    required = bool(spec.get("required"))
    field_type = (spec.get("type") or "").lower()

    if value is None or value == "":
        if required:
            errors.append("This field is required.")
            return None, errors
        default = spec.get("default")
        return default, errors

    if field_type == "bool":
        if isinstance(value, bool):
            return value, errors
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "on"}:
                return True, errors
            if text in {"false", "0", "no", "n", "off"}:
                return False, errors
            errors.append("Value must be 'true' or 'false'.")
            return None, errors
        if isinstance(value, (int, float)):
            return bool(value), errors
        errors.append("Invalid boolean value.")
        return None, errors

    if field_type == "enum":
        valid_choices = {choice[0] for choice in spec.get("choices", []) or []}
        if value not in valid_choices:
            errors.append("Select one of the available options.")
            return None, errors
        return value, errors

    if field_type in {"int", "float"}:
        try:
            coerced = float(value)
        except Exception:
            errors.append("Value must be a number.")
            return None, errors
        if math.isnan(coerced) or math.isinf(coerced):
            errors.append("Value must be a finite number.")
            return None, errors
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is not None:
            coerced = max(coerced, float(min_val))
        if max_val is not None:
            coerced = min(coerced, float(max_val))
        if field_type == "int":
            return int(round(coerced)), errors
        precision = spec.get("precision")
        if precision is None:
            precision = spec.get("decimals")
        if isinstance(precision, int) and precision >= 0:
            coerced = round(coerced, precision)
        return float(coerced), errors

    return value, errors


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


class TemplateMatchTool:
    def prepare(self, context: ToolRunnerContext) -> None:  # pragma: no cover - no-op
        return None

    def run(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: ToolRunnerContext,
    ) -> ToolRunResult:
        start = time.perf_counter()

        golden_u8 = _ensure_gray_u8(golden)
        frame_u8 = _ensure_gray_u8(frame)

        params_dict = dict(params.values or {})
        thresholds_dict = dict(thresholds.values or {})

        roi_value = params_dict.pop("__roi__", None)
        roi = ToolRoi.from_obj(roi_value)
        roi_rect = roi.rect()

        fh, fw = frame_u8.shape[:2]
        gh, gw = golden_u8.shape[:2]
        search_rect = _clamp_rect(roi_rect or (0, 0, fw, fh), fw, fh)

        template_source = ToolRoi.from_obj(params_dict.get("template_roi")).rect()
        use_golden_crop = bool(params_dict.get("use_golden_crop", True))
        if template_source is None and use_golden_crop:
            template_source = search_rect
        template_rect = _clamp_rect(template_source, gw, gh)

        coarse_cap = max(1, _safe_int(params_dict.get("coarse_cap", 600), 600))
        threshold_corr = float(_safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55))
        apply_alignment = bool(params_dict.get("apply_alignment", True))

        dx = 0.0
        dy = 0.0
        corr = 0.0
        used = 0

        if template_rect is not None and search_rect is not None:
            tx, ty, tw, th = template_rect
            sx, sy, sw, sh = search_rect
            templ = golden_u8[ty : ty + th, tx : tx + tw]
            if templ.size > 0 and sw >= tw and sh >= th:
                dx_rel, dy_rel, corr, used = imaging.match_template_u8(
                    frame_u8,
                    templ,
                    roi=(sx, sy, sw, sh),
                    search_margin=0,
                    coarse_cap=int(coarse_cap),
                )
                dx = float((sx + dx_rel) - tx)
                dy = float((sy + dy_rel) - ty)

        status: Literal["ok", "nok", "warn"]
        if used == 0:
            status = "warn"
        elif corr >= threshold_corr:
            status = "ok"
        else:
            status = "nok"

        T = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
        context.T_total = compose_affine(context.T_total, T)

        source = context.frame_aligned if context.frame_aligned is not None else context.frame
        if source is None:
            source = frame_u8
        if apply_alignment:
            context.frame_aligned = imaging.warp_by_translation_u8(source, -dx, -dy)
            context.frame_is_aligned = True
        else:
            context.frame_aligned = context.frame
            context.frame_is_aligned = False

        latency_ms = (time.perf_counter() - start) * 1000.0
        metrics = {"dx": dx, "dy": dy, "corr": corr, "latency_ms": latency_ms}
        debug = {
            "dx": dx,
            "dy": dy,
            "corr": corr,
            "latency_ms": latency_ms,
            "T": T,
            "status": status,
            "search_roi": _rect_to_dict(search_rect),
            "template_roi": _rect_to_dict(template_rect),
            "apply_alignment": apply_alignment,
        }
        return ToolRunResult(status=status, metrics=metrics, latency_ms=latency_ms, debug_artifacts=debug)

    def teardown(self) -> None:  # pragma: no cover - no-op
        return None


class SsimTool:
    def prepare(self, context: ToolRunnerContext) -> None:  # pragma: no cover - no-op
        return None

    def run(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: ToolRunnerContext,
    ) -> ToolRunResult:
        start = time.perf_counter()

        golden_u8 = _ensure_gray_u8(golden)
        frame_u8 = _ensure_gray_u8(frame)
        frame_orig = _ensure_gray_u8(context.frame)

        thresholds_dict = dict(thresholds.values or {})
        roi_value = params.values.get("__roi__") if isinstance(params.values, dict) else None
        roi = ToolRoi.from_obj(roi_value)

        gh, gw = golden_u8.shape[:2]
        roi_rect = _clamp_rect(roi.rect() if roi else None, gw, gh)
        if roi_rect is None:
            roi_rect = (0, 0, gw, gh)

        ssim_min = float(_safe_float(thresholds_dict.get("ssim_min", 0.92), 0.92))

        dx_total, dy_total = _extract_translation_from_affine(context.T_total)
        frame_eval = frame_u8
        virtual_alignment = False
        if not context.frame_is_aligned and (abs(dx_total) > 1e-3 or abs(dy_total) > 1e-3):
            frame_eval = imaging.warp_by_translation_u8(frame_orig, -dx_total, -dy_total)
            virtual_alignment = True

        x, y, w, h = roi_rect
        golden_crop = golden_u8[y : y + h, x : x + w]
        frame_crop = frame_eval[y : y + h, x : x + w]
        ssim_val = float(imaging.ssim_u8(golden_crop, frame_crop))

        status: Literal["ok", "nok", "warn"] = "ok" if ssim_val >= ssim_min else "nok"

        latency_ms = (time.perf_counter() - start) * 1000.0
        metrics = {"ssim": round(ssim_val, 5), "latency_ms": latency_ms}
        debug = {
            "ssim": ssim_val,
            "ssim_min": ssim_min,
            "virtual_alignment": virtual_alignment,
            "roi": _rect_to_dict(roi_rect),
            "dx_total": dx_total,
            "dy_total": dy_total,
            "latency_ms": latency_ms,
            "status": status,
        }
        return ToolRunResult(status=status, metrics=metrics, latency_ms=latency_ms, debug_artifacts=debug)

    def teardown(self) -> None:  # pragma: no cover - no-op
        return None


# ---------------------------------------------------------------------------
# Registry bootstrap
# ---------------------------------------------------------------------------


def _register_builtin_tools() -> None:
    registry.register(
        ToolDefinition(
            type="locator.template_match",
            display_name="Locator (Template Match)",
            meta=ToolDefinitionMeta(
                description="Vyhľadávanie šablóny s podporou search a template ROI.",
                category="Locator",
                supports_roi=True,
                schema=ToolDefinitionMetaSchema(
                    params={
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
                    thresholds={
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
                ),
            ),
            metrics_spec={
                "dx": ToolMetricSpec(key="dx", unit="px", priority=1, label="Δx"),
                "dy": ToolMetricSpec(key="dy", unit="px", priority=1, label="Δy"),
                "corr": ToolMetricSpec(key="corr", label="Correlation", priority=2),
            },
        ),
        factory=lambda: TemplateMatchTool(),
    )

    registry.register(
        ToolDefinition(
            type="ssim",
            display_name="SSIM",
            meta=ToolDefinitionMeta(
                description="Porovnanie štrukturálnej podobnosti v ROI.",
                category="Similarity",
                supports_roi=True,
                supports_ignore_mask=True,
                schema=ToolDefinitionMetaSchema(
                    params={},
                    thresholds={
                        "ssim_min": {
                            "type": "float",
                            "label": "ssim_min",
                            "default": 0.92,
                            "min": 0.0,
                            "max": 1.0,
                            "step": 0.01,
                            "precision": 3,
                            "required": True,
                            "description": "Minimálna povolená hodnota štrukturálnej podobnosti (SSIM).",
                        }
                    },
                ),
            ),
            metrics_spec={
                "ssim": ToolMetricSpec(key="ssim", priority=0, label="SSIM"),
            },
        ),
        factory=lambda: SsimTool(),
    )


_register_builtin_tools()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def validate_tool_params(
    type_id: str,
    params: Optional[Dict[str, Any]],
    thresholds: Optional[Dict[str, Any]],
) -> tuple[bool, Dict[str, Dict[str, list[str]]], Dict[str, Dict[str, Any]]]:
    schema = registry.get_schema(type_id)
    param_specs = schema.get("params", {})
    threshold_specs = schema.get("thresholds", {})

    params_input = dict(params or {})
    thresholds_input = dict(thresholds or {})

    normalized_params = dict(params_input)
    normalized_thresholds = dict(thresholds_input)
    param_errors: Dict[str, list[str]] = {}
    threshold_errors: Dict[str, list[str]] = {}

    for name, spec in param_specs.items():
        value, errors = _normalize_field_value(params_input.get(name), spec)
        if errors:
            param_errors[name] = errors
        if value is None:
            normalized_params.pop(name, None)
        else:
            normalized_params[name] = value

    for name, spec in threshold_specs.items():
        value, errors = _normalize_field_value(thresholds_input.get(name), spec)
        if errors:
            threshold_errors[name] = errors
        if value is None:
            normalized_thresholds.pop(name, None)
        else:
            normalized_thresholds[name] = value

    ok = not param_errors and not threshold_errors
    errors = {"params": param_errors, "thresholds": threshold_errors}
    normalized = {"params": normalized_params, "thresholds": normalized_thresholds}
    return ok, errors, normalized


def _validate_roi(tool: Tool, definition: ToolDefinition) -> None:
    roi_rect = tool.roi.rect()
    if roi_rect is None:
        return
    if not definition.meta.supports_roi:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ROI but one was provided"
        )
    _, _, w, h = roi_rect
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid ROI dimensions for tool '{tool.name}'")


def _validate_ignore_mask(tool: Tool, definition: ToolDefinition) -> None:
    mask = tool.ignore_mask.value
    if mask is None:
        return
    if not definition.meta.supports_ignore_mask:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ignore mask"
        )
    if mask.ndim != 2:
        raise ValueError(f"Ignore mask for tool '{tool.name}' must be 2D")


def _ensure_locator_first(tools: Sequence[Tool]) -> list[Tool]:
    locators = [tool for tool in tools if tool.type.startswith("locator.")]
    analyzers = [tool for tool in tools if not tool.type.startswith("locator.")]
    return [*locators, *analyzers]


def run_pipeline(
    recipe: RecipeV2,
    golden: np.ndarray,
    frame: np.ndarray,
) -> Tuple[ToolRunnerContext, list[Dict[str, Any]], list[ToolRunResult]]:
    context = ToolRunnerContext(
        frame=frame,
        frame_aligned=frame,
        T_total=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        frame_is_aligned=False,
    )

    diagnostics: list[Dict[str, Any]] = []
    results: list[ToolRunResult] = []

    tools: Sequence[Tool] = sorted(recipe.tools, key=lambda t: t.order)
    tools = _ensure_locator_first(tools)

    for tool in tools:
        definition = registry.get_definition(tool.type)
        if definition is None:
            raise ValueError(f"Tool type '{tool.type}' is not registered")

        _validate_roi(tool, definition)
        _validate_ignore_mask(tool, definition)

        tool_id = tool.name or f"tool_{tool.order}"

        if not tool.enabled:
            diagnostics.append({"tool_id": tool_id, "type": tool.type, "status": "disabled"})
            continue

        runner = registry.create(tool.type)
        runner.prepare(context)

        params_obj = ToolParams(dict(tool.params.values or {}))
        params_obj.values["__roi__"] = tool.roi.to_dict()
        thresholds_obj = ToolThresholds(dict(tool.thresholds.values or {}))

        frame_for_tool = context.frame_aligned if context.frame_aligned is not None else context.frame
        if frame_for_tool is None:
            raise ValueError("Frame not available for tool execution")

        result = runner.run(golden, frame_for_tool, params_obj, thresholds_obj, context)
        runner.teardown()

        results.append(result)
        diag_payload = {"tool_id": tool_id, "type": tool.type, "status": result.status}
        if result.debug_artifacts:
            diag_payload.update(dict(result.debug_artifacts))
        diagnostics.append(diag_payload)

    return context, diagnostics, results


def run_tool_isolated(
    tool_type: str,
    *,
    golden: np.ndarray,
    context: ToolRunnerContext,
    params: ToolParams | Dict[str, Any] | None,
    thresholds: ToolThresholds | Dict[str, Any] | None,
    frame: np.ndarray | None = None,
) -> ToolRunResult:
    if golden is None:
        raise ValueError("Golden image is required")
    if context.frame is None and frame is None:
        raise ValueError("Frame image is required for isolated tool run")

    if context.frame is None and frame is not None:
        context.frame = np.asarray(frame)
    if context.frame_aligned is None:
        context.frame_aligned = context.frame
    if context.T_total is None:
        context.T_total = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)

    params_obj = params if isinstance(params, ToolParams) else ToolParams.from_obj(params)
    thresholds_obj = (
        thresholds
        if isinstance(thresholds, ToolThresholds)
        else ToolThresholds.from_obj(thresholds)
    )

    roi_value = None
    if isinstance(params_obj.values, dict) and "__roi__" in params_obj.values:
        roi_value = params_obj.values["__roi__"]
    elif isinstance(thresholds_obj.values, dict) and "__roi__" in thresholds_obj.values:
        roi_value = thresholds_obj.values["__roi__"]
    if roi_value is not None and isinstance(params_obj.values, dict):
        params_obj.values["__roi__"] = roi_value

    frame_for_tool = context.frame_aligned if context.frame_aligned is not None else context.frame
    if frame_for_tool is None:
        raise ValueError("Frame not available for tool execution")

    runner = registry.create(tool_type)
    runner.prepare(context)
    result = runner.run(golden, frame_for_tool, params_obj, thresholds_obj, context)
    runner.teardown()
    return result


__all__ = [
    "DEFAULT_THRESHOLDS",
    "ITool",
    "ToolMeta",
    "ToolMetaView",
    "ToolRunResult",
    "ToolRunnerContext",
    "ToolService",
    "ToolRegistry",
    "compose_affine",
    "run_locator_template_match",
    "run_pipeline",
    "run_tool_isolated",
    "validate_tool_params",
]

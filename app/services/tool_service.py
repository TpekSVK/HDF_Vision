# app/services/tool_service.py
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Protocol, runtime_checkable, TYPE_CHECKING

import math
import time

import imageio.v3 as iio
import numpy as np

from app.services.compare_service import analyze
from app.services import logging_service, settings_service
from app.models.schema import (
    RecipeData,
    RecipeV2,
    Tool,
    ToolDefinition,
    ToolMask,
    ToolParams,
    ToolRoi,
    ToolThresholds,
)
from app.utils import overlay as overlay_utils
from app.utils.tool_identity import compute_tool_identity

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    import numpy as np


@dataclass(slots=True)
class ToolRunResult:
    """Normalized result returned by tool runners."""

    status: Literal["ok", "nok", "warn"]
    metrics: Dict[str, Any]
    latency_ms: float
    debug_artifacts: Optional[Dict[str, Any]] = None


class LatencyTracker:
    """In-memory circular buffer storing per-tool latencies."""

    def __init__(self, capacity: int = 64) -> None:
        self.capacity = max(1, int(capacity))
        self._history: Dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.capacity))

    def record(self, tool_id: str, latency_ms: float) -> None:
        if not tool_id:
            return
        self._history[tool_id].append(float(latency_ms))

    def summary(self) -> Dict[str, Dict[str, float]]:
        stats: Dict[str, Dict[str, float]] = {}
        for tool_id, values in self._history.items():
            if not values:
                continue
            samples = list(values)
            stats[tool_id] = {
                "count": int(len(samples)),
                "avg_ms": float(sum(samples) / len(samples)),
                "min_ms": float(min(samples)),
                "max_ms": float(max(samples)),
                "last_ms": float(samples[-1]),
            }
        return stats

    def history(self) -> Dict[str, List[float]]:
        return {tool_id: list(values) for tool_id, values in self._history.items()}


_LATENCY_TRACKER = LatencyTracker()


def record_tool_latency(tool_id: str, latency_ms: float) -> None:
    _LATENCY_TRACKER.record(tool_id, latency_ms)


@runtime_checkable
class ITool(Protocol):
    """Common interface that all tool implementations must follow."""

    def prepare(self, context: dict[str, Any]) -> None:
        ...

    def run(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        ...

    def teardown(self) -> None:
        ...


class BaseTool:
    """Base helper implementing shared lifecycle for tools."""

    def __init__(self) -> None:
        self._prepared_context: dict[str, Any] = {}
        self.last_diagnostics: dict[str, Any] = {}

    def prepare(self, context: dict[str, Any]) -> None:  # type: ignore[override]
        self._prepared_context = dict(context or {})
        self.last_diagnostics = {}

    def teardown(self) -> None:  # type: ignore[override]
        self._prepared_context.clear()

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        raise NotImplementedError


class LocatorTemplateMatchTool(BaseTool):
    """ITool implementation wrapping template matching locator."""

    def __init__(self) -> None:
        super().__init__()
        self._cache_signature: Any | None = None
        self._match_cache: dict[str, Any] = {}

    def prepare(self, context: dict[str, Any]) -> None:  # type: ignore[override]
        super().prepare(context)
        self._cache_signature = None
        self._match_cache.clear()

    def teardown(self) -> None:  # type: ignore[override]
        super().teardown()
        self._cache_signature = None
        self._match_cache.clear()

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        tool: Optional[Tool] = self._prepared_context.get("tool")
        roi = tool.roi if isinstance(tool, Tool) else context.get("roi", ToolRoi())
        tool_id = self._prepared_context.get("tool_id") or (tool.name if isinstance(tool, Tool) else "locator")

        params_obj = params if isinstance(params, ToolParams) else ToolParams.from_obj(params)
        thresholds_obj = (
            thresholds if isinstance(thresholds, ToolThresholds) else ToolThresholds.from_obj(thresholds)
        )

        params_dict = params_obj.values or {}
        thresholds_dict = thresholds_obj.values or {}

        template_signature = (
            tuple(np.asarray(frame).shape[:2]),
            (
                bool(params_dict.get("use_golden_crop", True)),
                _freeze_value(params_dict.get("template_roi")),
                _safe_int(params_dict.get("coarse_cap", 600), 600),
                bool(params_dict.get("rotation_enabled", False)),
                round(_safe_float(params_dict.get("angle_range_deg", 15.0), 15.0), 4),
                round(_safe_float(params_dict.get("angle_step_deg", 1.0), 1.0), 4),
                bool(params_dict.get("angle_enabled", False)),
                _freeze_value(params_dict.get("angle_roi")),
                str(params_dict.get("angle_method", "fitline")),
                round(_safe_float(params_dict.get("angle_ref_deg", 0.0), 0.0), 4),
                round(_safe_float(params_dict.get("angle_max_dev_deg", 15.0), 15.0), 4),
                round(_safe_float(params_dict.get("angle_smooth", 0.0), 0.0), 4),
            ),
            (_safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55),),
        )
        if template_signature != self._cache_signature:
            self._cache_signature = template_signature
            self._match_cache.clear()

        result, diagnostics = run_locator_template_match(
            golden,
            frame,
            params_obj,
            thresholds_obj,
            roi,
            tool_id=str(tool_id),
            cache=self._match_cache,
        )
        self.last_diagnostics = diagnostics
        return result


class SSIMTool(BaseTool):
    """ITool implementation for SSIM measurement."""

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        tool: Optional[Tool] = self._prepared_context.get("tool")
        roi = tool.roi if isinstance(tool, Tool) else context.get("roi", ToolRoi())
        tool_id = self._prepared_context.get("tool_id") or (tool.name if isinstance(tool, Tool) else "ssim")

        thresholds_obj = (
            thresholds if isinstance(thresholds, ToolThresholds) else ToolThresholds.from_obj(thresholds)
        )

        runner_context: ToolRunnerContext | None = self._prepared_context.get("runner_context")
        if runner_context is None:
            raise ValueError("Runner context missing for SSIM tool execution")

        frame_original = runner_context.frame
        T_total = runner_context.T_total
        frame_is_aligned = runner_context.frame_is_aligned

        result, diagnostics = run_ssim_tool(
            golden,
            frame,
            thresholds_obj,
            roi,
            frame_original=frame_original,
            T_total=T_total,
            frame_is_aligned=frame_is_aligned,
            tool_id=str(tool_id),
        )
        self.last_diagnostics = diagnostics
        return result


class AbsDiffTool(BaseTool):
    """Placeholder implementation for absolute difference inspection tool."""

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        start = time.perf_counter()
        latency_ms = (time.perf_counter() - start) * 1000.0
        return ToolRunResult(
            status="warn",
            metrics={"latency_ms": latency_ms},
            latency_ms=latency_ms,
            debug_artifacts={"message": "AbsDiff tool is not yet implemented"},
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

    from app.services.tool_registry import ToolRegistry

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

    def latency_summary(self) -> Dict[str, Dict[str, float]]:
        """Return aggregate latency stats for the most recent tool runs."""

        return _LATENCY_TRACKER.summary()

    def latency_history(self) -> Dict[str, List[float]]:
        """Return raw latency history per tool."""

        return _LATENCY_TRACKER.history()

    # ------------------------------------------------------------------
    # Tool registry API
    # ------------------------------------------------------------------
    def list_tool_types(self) -> List[str]:
        """Return registered tool types."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.list_tool_types()

    def get_tool_meta(self, tool_type: str) -> ToolDefinition:
        """Retrieve metadata for a given tool type."""

        from app.services.tool_registry import ToolRegistry

        definition = ToolRegistry.get_tool_definition(tool_type)
        if definition is None:
            raise KeyError(f"Tool type '{tool_type}' is not registered")
        return definition

    def get_tool_schema(self, tool_type: str) -> Dict[str, Dict[str, Any]]:
        """Expose registry schema information for UI consumption."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.get_tool_schema(tool_type)

    def make_default_tool(self, tool_type: str, name: Optional[str] = None) -> Tool:
        """Create a ``Tool`` instance with registry defaults."""

        from app.services.tool_registry import ToolRegistry

        return ToolRegistry.make_default_tool(tool_type, name=name)

    def load_recipe(self, name: str):
        import numpy as np

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
    frame_gray: np.ndarray | None = None
    frame_aligned_gray: np.ndarray | None = None
    golden_gray: np.ndarray | None = None


@dataclass(slots=True)
class PipelineToolReport:
    """Aggregated result for a single tool within the pipeline."""

    tool: Tool
    tool_id: str
    order: int
    status: Literal["ok", "nok", "warn"]
    metrics: Dict[str, Any]
    latency_ms: float
    diagnostics: Dict[str, Any]
    overlay_items: list[overlay_utils.OverlayItem] = field(default_factory=list)


@dataclass(slots=True)
class PipelineResult:
    """Result of executing the entire tool pipeline."""

    context: ToolRunnerContext
    per_tool: List[PipelineToolReport]
    diagnostics: List[Dict[str, Any]]
    cycle_time_ms: float
    status: Literal["ok", "nok", "warn"]
    policy_applied: Optional[str] = None
    overlay_items: List[overlay_utils.OverlayItem] = field(default_factory=list)


@dataclass(slots=True)
class ToolTestRun:
    """Result of executing a partial pipeline for tool testing."""

    result: ToolRunResult
    report: PipelineToolReport
    reports: List[PipelineToolReport]
    diagnostics: List[Dict[str, Any]]
    context: ToolRunnerContext
    elapsed_ms: float
    policy_applied: Optional[str] = None
    overlay_items: List[overlay_utils.OverlayItem] = field(default_factory=list)


def compose_affine(
    T_total: np.ndarray | None, T_new: np.ndarray | None
) -> np.ndarray:
    """Left-compose 2×3 affine transforms.

    The resulting matrix corresponds to applying ``T_total`` first and then
    ``T_new`` (``T_new ∘ T_total``). ``None`` inputs are treated as identity
    transforms.
    """

    import numpy as np

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


def _identity_affine() -> np.ndarray:
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)


def _validate_roi(tool: Tool, definition: ToolDefinition) -> None:
    roi_rect = tool.roi.rect()
    if roi_rect is None:
        return
    if not definition.meta.supports_roi:
        raise ValueError(
            f"Tool '{tool.name}' of type '{tool.type}' does not support ROI but one was provided"
        )
    x, y, w, h = roi_rect
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


def _validate_params(tool: Tool) -> None:
    params = tool.params.values
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError(f"Params for tool '{tool.name}' must be a dictionary")


def _apply_regions_from_params(tool: Tool) -> None:
    """Synchronize ``tool.roi`` and ``tool.ignore_mask`` from stored params."""

    params_values = dict(getattr(tool.params, "values", {}) or {})

    if "roi" in params_values:
        try:
            tool.roi = ToolRoi.from_obj(params_values.get("roi"))
        except Exception:
            tool.roi = ToolRoi()

    if "ignore_mask" in params_values:
        try:
            tool.ignore_mask = ToolMask.from_obj(params_values.get("ignore_mask"))
        except Exception:
            tool.ignore_mask = ToolMask(None)


class PipelineOrchestrator:
    """Central orchestrator ensuring ordered tool execution with shared context."""

    _LOCATOR_PREFIX = "locator."

    def run_pipeline(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        recipe: RecipeV2,
        recipe_name: str | None = None,
        notes: str | None = None,
    ) -> PipelineResult:
        """Execute the configured pipeline and return aggregated results."""

        import numpy as np
        from app.services.tool_registry import ToolRegistry
        from app.utils import imaging

        start_time = time.perf_counter()

        golden_array = np.asarray(golden)
        frame_array = np.asarray(frame)
        golden_gray = imaging.to_gray_u8(golden_array)
        frame_gray = imaging.to_gray_u8(frame_array)

        context = ToolRunnerContext(
            frame=frame_array,
            frame_aligned=frame_array,
            T_total=_identity_affine(),
            frame_is_aligned=False,
            frame_gray=frame_gray,
            frame_aligned_gray=frame_gray,
            golden_gray=golden_gray,
        )

        diagnostics: List[Dict[str, Any]] = []
        per_tool: List[PipelineToolReport] = []
        policy_applied: Optional[str] = None
        session_settings = settings_service.get_session_settings()
        logging_enabled = session_settings.logging_enabled and bool(
            getattr(recipe, "logging_enabled", True)
        )
        collect_overlay = (
            logging_enabled
            and session_settings.export_artifacts
            and session_settings.export_overlay
            and bool(getattr(recipe, "export_artifacts", False))
        )

        overlay_palette = overlay_utils.default_palette() if collect_overlay else []
        overlay_index = 0
        pipeline_overlay_items: List[overlay_utils.OverlayItem] = []

        failure_policy = self._normalize_failure_policy(
            getattr(recipe, "on_locator_failure", "continue_without_alignment")
        )
        tools = self._order_tools(recipe.tools)

        used_tool_ids: set[str] = set()

        for index, tool in enumerate(tools):
            definition = ToolRegistry.get_tool_definition(tool.type)
            if definition is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")

            _apply_regions_from_params(tool)
            _validate_roi(tool, definition)
            _validate_ignore_mask(tool, definition)
            _validate_params(tool)

            tool_id, tool_label, tool_order = compute_tool_identity(
                tool,
                fallback_index=index,
                used_ids=used_tool_ids,
            )
            diag_entry: Dict[str, Any] = {
                "tool_id": tool_id,
                "tool": tool_label,
                "type": tool.type,
                "order": tool_order,
                "status": "skipped",
            }

            if not tool.enabled:
                diag_entry["disabled"] = True
                diagnostics.append(diag_entry)
                continue

            runner = ToolRegistry.create_tool(tool.type)
            runner.prepare({"tool": tool, "tool_id": tool_id, "runner_context": context})

            frame_for_tool = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if frame_for_tool is None:
                raise ValueError("Frame data not available for tool execution")

            result = runner.run(
                golden_array,
                frame_for_tool,
                tool.params,
                tool.thresholds,
                {"roi": tool.roi},
            )
            diagnostics_payload = getattr(runner, "last_diagnostics", {})
            diag_data = diagnostics_payload if isinstance(diagnostics_payload, dict) else {}
            runner.teardown()

            if isinstance(diagnostics_payload, dict):
                diag_entry.update(diagnostics_payload)
            if result.debug_artifacts and isinstance(
                result.debug_artifacts.get("diagnostics"), dict
            ):
                diag_entry.update(result.debug_artifacts["diagnostics"])

            diag_entry["status"] = result.status
            diag_entry.setdefault("latency_ms", float(result.latency_ms))

            metrics = dict(result.metrics or {})
            metrics.setdefault("latency_ms", float(result.latency_ms))

            tool_overlay_items: List[overlay_utils.OverlayItem] = []
            if collect_overlay:
                if overlay_palette:
                    tool_color = overlay_palette[overlay_index % len(overlay_palette)]
                    overlay_index += 1
                else:  # pragma: no cover - defensive fallback
                    tool_color = (255, 0, 0)

                display_sources: List[Any] = []
                display_sources.extend(
                    overlay_utils.extract_display_items_from_artifacts(
                        result.debug_artifacts
                    )
                )
                if isinstance(diagnostics_payload, dict):
                    display_sources.extend(
                        overlay_utils.extract_display_items_from_artifacts(
                            diagnostics_payload
                        )
                    )

                overlay_affine = None
                if self._is_locator(tool):
                    overlay_affine = compose_affine(context.T_total, diag_entry.get("T"))
                else:
                    overlay_affine = context.T_total

                tool_overlay_items = overlay_utils.tool_overlay_items(
                    tool,
                    color=tool_color,
                    display_items=display_sources,
                    label=str(tool_label),
                    affine=overlay_affine,
                )
                pipeline_overlay_items.extend(tool_overlay_items)

            diagnostics.append(diag_entry)
            record_tool_latency(str(tool_id), float(result.latency_ms))

            per_tool.append(
                PipelineToolReport(
                    tool=tool.copy(),
                    tool_id=str(tool_id),
                    order=int(tool_order),
                    status=result.status,
                    metrics=metrics,
                    latency_ms=float(result.latency_ms),
                    diagnostics=dict(diag_entry),
                    overlay_items=tool_overlay_items,
                )
            )

            if self._is_locator(tool):
                locator_found = bool(metrics.get("found", diag_data.get("found", True)))
                corr_value = _safe_float(metrics.get("corr", diag_data.get("corr")), 0.0)
                thresholds_map = dict(getattr(tool.thresholds, "values", {}) or {})
                threshold_raw = diag_data.get("threshold_corr", thresholds_map.get("threshold_corr"))
                threshold_corr = _safe_float(threshold_raw, 0.55)

                diag_entry["found"] = locator_found
                diag_entry["corr"] = corr_value
                diag_entry["threshold_corr"] = threshold_corr

                locator_failure = False
                failure_reason = None
                if not locator_found:
                    locator_failure = True
                    failure_reason = "not_found"
                elif corr_value < threshold_corr:
                    locator_failure = True
                    failure_reason = "low_corr"
                elif result.status == "nok":
                    locator_failure = True
                    failure_reason = "status_nok"

                if locator_failure:
                    diag_entry["locator_failure"] = True
                    if failure_reason:
                        diag_entry["locator_failure_reason"] = failure_reason
                    diag_entry["policy_applied"] = failure_policy
                    policy_applied = policy_applied or failure_policy
                    self._reset_alignment(context)
                    if failure_policy == "fail":
                        break
                else:
                    self._apply_locator_alignment(tool, context, diag_entry)

        cycle_time_ms = (time.perf_counter() - start_time) * 1000.0
        pipeline_status = self._aggregate_status(per_tool)

        result = PipelineResult(
            context=context,
            per_tool=per_tool,
            diagnostics=diagnostics,
            cycle_time_ms=float(cycle_time_ms),
            status=pipeline_status,
            policy_applied=policy_applied,
            overlay_items=pipeline_overlay_items if collect_overlay else [],
        )

        try:
            logging_service.record_pipeline_run(
                recipe=recipe,
                recipe_name=recipe_name,
                result=result,
                notes=notes,
            )
        except Exception as exc:  # pragma: no cover - logging must not break pipeline
            print("[pipeline][log][err]", exc)

        return result

    def run_tool_test(
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        recipe: RecipeV2,
    ) -> ToolTestRun:
        """Execute tools up to the last entry in ``recipe`` for wizard tests."""

        import numpy as np
        from app.services.tool_registry import ToolRegistry
        from app.utils import imaging

        start_time = time.perf_counter()

        golden_array = np.asarray(golden)
        frame_array = np.asarray(frame)
        golden_gray = imaging.to_gray_u8(golden_array)
        frame_gray = imaging.to_gray_u8(frame_array)

        context = ToolRunnerContext(
            frame=frame_array,
            frame_aligned=frame_array,
            T_total=_identity_affine(),
            frame_is_aligned=False,
            frame_gray=frame_gray,
            frame_aligned_gray=frame_gray,
            golden_gray=golden_gray,
        )

        diagnostics: List[Dict[str, Any]] = []
        per_tool: List[PipelineToolReport] = []
        policy_applied: Optional[str] = None

        failure_policy = self._normalize_failure_policy(
            getattr(recipe, "on_locator_failure", "continue_without_alignment")
        )
        tools = self._order_tools(recipe.tools)
        if not tools:
            raise ValueError("Recipe does not contain any tools")

        collect_overlay = True
        overlay_palette = overlay_utils.default_palette() if collect_overlay else []
        overlay_index = 0
        pipeline_overlay_items: List[overlay_utils.OverlayItem] = []

        target_result: ToolRunResult | None = None
        target_report: PipelineToolReport | None = None

        used_tool_ids: set[str] = set()

        for index, tool in enumerate(tools):
            definition = ToolRegistry.get_tool_definition(tool.type)
            if definition is None:
                raise ValueError(f"Tool type '{tool.type}' is not registered")

            _apply_regions_from_params(tool)
            _validate_roi(tool, definition)
            _validate_ignore_mask(tool, definition)
            _validate_params(tool)

            tool_id, tool_label, tool_order = compute_tool_identity(
                tool,
                fallback_index=index,
                used_ids=used_tool_ids,
            )
            diag_entry: Dict[str, Any] = {
                "tool_id": tool_id,
                "tool": tool_label,
                "type": tool.type,
                "order": tool_order,
                "status": "skipped",
            }

            if not tool.enabled:
                diag_entry["disabled"] = True
                diagnostics.append(diag_entry)
                continue

            runner = ToolRegistry.create_tool(tool.type)
            runner.prepare({"tool": tool, "tool_id": tool_id, "runner_context": context})

            frame_for_tool = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if frame_for_tool is None:
                raise ValueError("Frame data not available for tool execution")

            result = runner.run(
                golden_array,
                frame_for_tool,
                tool.params,
                tool.thresholds,
                {"roi": tool.roi},
            )
            diagnostics_payload = getattr(runner, "last_diagnostics", {})
            runner.teardown()

            if isinstance(diagnostics_payload, dict):
                diag_entry.update(diagnostics_payload)
            if result.debug_artifacts and isinstance(
                result.debug_artifacts.get("diagnostics"), dict
            ):
                diag_entry.update(result.debug_artifacts["diagnostics"])

            diag_entry["status"] = result.status
            diag_entry.setdefault("latency_ms", float(result.latency_ms))

            metrics = dict(result.metrics or {})
            metrics.setdefault("latency_ms", float(result.latency_ms))
            result.metrics = metrics

            tool_overlay_items: List[overlay_utils.OverlayItem] = []
            if collect_overlay:
                if overlay_palette:
                    tool_color = overlay_palette[overlay_index % len(overlay_palette)]
                    overlay_index += 1
                else:  # pragma: no cover - defensive fallback
                    tool_color = (255, 0, 0)

                display_sources: List[Any] = []
                display_sources.extend(
                    overlay_utils.extract_display_items_from_artifacts(result.debug_artifacts)
                )
                if isinstance(diagnostics_payload, dict):
                    display_sources.extend(
                        overlay_utils.extract_display_items_from_artifacts(
                            diagnostics_payload
                        )
                    )

                overlay_affine = None
                if self._is_locator(tool):
                    overlay_affine = compose_affine(context.T_total, diag_entry.get("T"))
                else:
                    overlay_affine = context.T_total

                tool_overlay_items = overlay_utils.tool_overlay_items(
                    tool,
                    color=tool_color,
                    display_items=display_sources,
                    label=str(tool_label),
                    affine=overlay_affine,
                )
                pipeline_overlay_items.extend(tool_overlay_items)

            diagnostics.append(diag_entry)
            record_tool_latency(str(tool_id), float(result.latency_ms))

            report = PipelineToolReport(
                tool=tool.copy(),
                tool_id=str(tool_id),
                order=int(tool_order),
                status=result.status,
                metrics=dict(metrics),
                latency_ms=float(result.latency_ms),
                diagnostics=dict(diag_entry),
                overlay_items=tool_overlay_items,
            )
            per_tool.append(report)

            if self._is_locator(tool):
                diag_data = diagnostics_payload if isinstance(diagnostics_payload, dict) else {}
                locator_found = bool(metrics.get("found", diag_data.get("found", True)))
                corr_value = _safe_float(metrics.get("corr", diag_data.get("corr")), 0.0)
                thresholds_map = dict(getattr(tool.thresholds, "values", {}) or {})
                threshold_raw = diag_data.get("threshold_corr", thresholds_map.get("threshold_corr"))
                threshold_corr = _safe_float(threshold_raw, 0.55)

                diag_entry["found"] = locator_found
                diag_entry["corr"] = corr_value
                diag_entry["threshold_corr"] = threshold_corr

                locator_failure = False
                failure_reason: Optional[str] = None
                if not locator_found:
                    locator_failure = True
                    failure_reason = "not_found"
                elif corr_value < threshold_corr:
                    locator_failure = True
                    failure_reason = "low_corr"
                elif result.status == "nok":
                    locator_failure = True
                    failure_reason = "status_nok"

                if locator_failure:
                    diag_entry["locator_failure"] = True
                    if failure_reason:
                        diag_entry["locator_failure_reason"] = failure_reason
                    diag_entry["policy_applied"] = failure_policy
                    policy_applied = policy_applied or failure_policy
                    self._reset_alignment(context)
                    if failure_policy == "fail":
                        break
                else:
                    self._apply_locator_alignment(tool, context, diag_entry)

            if index == len(tools) - 1:
                target_result = result
                target_report = report

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if target_result is None or target_report is None:
            failure_entry = next(
                (entry for entry in reversed(diagnostics) if entry.get("locator_failure")),
                None,
            )
            if failure_entry:
                tool_name = failure_entry.get("tool_id") or failure_entry.get("type") or "locator"
                reason = failure_entry.get("locator_failure_reason") or "locator failure"
                raise RuntimeError(
                    f"Pipeline stopped after locator '{tool_name}': {reason}."
                )
            raise RuntimeError("Target tool was not executed")

        return ToolTestRun(
            result=target_result,
            report=target_report,
            reports=per_tool,
            diagnostics=diagnostics,
            context=context,
            elapsed_ms=float(elapsed_ms),
            policy_applied=policy_applied,
            overlay_items=pipeline_overlay_items,
        )

    def _order_tools(self, tools: Sequence[Tool]) -> List[Tool]:
        sorted_tools = sorted(tools, key=lambda t: t.order)
        locators = [tool for tool in sorted_tools if self._is_locator(tool)]
        analyzers = [tool for tool in sorted_tools if not self._is_locator(tool)]
        return locators + analyzers

    def _is_locator(self, tool: Tool) -> bool:
        return tool.type.startswith(self._LOCATOR_PREFIX) or tool.type == "template_match"

    def _apply_locator_alignment(
        self, tool: Tool, context: ToolRunnerContext, diagnostics: Dict[str, Any]
    ) -> None:
        from app.utils import imaging

        T_new = diagnostics.get("T")
        context.T_total = compose_affine(context.T_total, T_new)

        params_dict = dict(tool.params.values or {})
        apply_alignment = bool(params_dict.get("apply_alignment", True))
        if apply_alignment:
            source = (
                context.frame_aligned if context.frame_aligned is not None else context.frame
            )
            if source is None:
                raise ValueError("Source frame missing for locator alignment")
            if context.frame_is_aligned and context.frame_aligned_gray is not None:
                source_gray = context.frame_aligned_gray
            elif source is context.frame and context.frame_gray is not None:
                source_gray = context.frame_gray
            else:
                source_gray = imaging.to_gray_u8(source)
            if T_new is None:
                context.frame_aligned = source
                context.frame_aligned_gray = source_gray
            else:
                T_inv = imaging.invert_affine(T_new)
                context.frame_aligned = imaging.warp_by_affine_u8(source, T_inv)
                context.frame_aligned_gray = imaging.warp_by_affine_u8(source_gray, T_inv)
            context.frame_is_aligned = True
        else:
            context.frame_aligned = context.frame
            context.frame_aligned_gray = context.frame_gray
            context.frame_is_aligned = False

    def _reset_alignment(self, context: ToolRunnerContext) -> None:
        context.T_total = _identity_affine()
        context.frame_aligned = context.frame
        context.frame_aligned_gray = context.frame_gray
        context.frame_is_aligned = False

    @staticmethod
    def _normalize_failure_policy(
        policy: str | None,
    ) -> Literal["fail", "continue_without_alignment"]:
        if not isinstance(policy, str):
            return "continue_without_alignment"
        normalized = policy.lower().strip()
        if normalized == "fail":
            return "fail"
        return "continue_without_alignment"

    @staticmethod
    def _aggregate_status(
        per_tool: Sequence[PipelineToolReport],
    ) -> Literal["ok", "nok", "warn"]:
        priority: Dict[str, int] = {"ok": 0, "warn": 1, "nok": 2}
        current: Literal["ok", "nok", "warn"] = "ok"
        for entry in per_tool:
            if priority[entry.status] > priority[current]:
                current = entry.status
        return current


def run_pipeline(
    golden: np.ndarray,
    frame: np.ndarray,
    recipe: RecipeV2,
    *,
    recipe_name: str | None = None,
    notes: str | None = None,
) -> PipelineResult:
    """Execute the configured pipeline using the shared orchestrator."""

    orchestrator = PipelineOrchestrator()
    return orchestrator.run_pipeline(
        golden,
        frame,
        recipe,
        recipe_name=recipe_name,
        notes=notes,
    )


def run_tool_test(
    golden: np.ndarray,
    frame: np.ndarray,
    recipe: RecipeV2,
) -> ToolTestRun:
    """Execute a partial pipeline for wizard tool tests."""

    orchestrator = PipelineOrchestrator()
    return orchestrator.run_tool_test(golden, frame, recipe)


def run_tool_isolated(
    tool_type: str,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    context: ToolRunnerContext,
    golden: np.ndarray,
    frame: np.ndarray | None,
) -> ToolRunResult:
    """Execute a single tool using an existing runner context."""

    import numpy as np
    from app.services.tool_registry import ToolRegistry
    from app.utils import imaging

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

    definition = ToolRegistry.get_tool_definition(tool_type)
    if definition is None:
        raise KeyError(f"Tool type '{tool_type}' is not registered")

    runner = ToolRegistry.create_tool(tool_type)

    params_obj = params if isinstance(params, ToolParams) else ToolParams(params_dict)
    thresholds_obj = (
        thresholds if isinstance(thresholds, ToolThresholds) else ToolThresholds(thresholds_dict)
    )

    tool_stub = Tool(
        type=tool_type,
        name=tool_type,
        params=params_obj,
        thresholds=thresholds_obj,
        roi=roi,
    )

    _apply_regions_from_params(tool_stub)
    runner.prepare({"tool": tool_stub, "tool_id": tool_type, "runner_context": context})

    frame_for_tool = context.frame_aligned if context.frame_aligned is not None else context.frame
    if frame_for_tool is None:
        raise ValueError("Frame not available for tool execution")

    result = runner.run(
        golden,
        frame_for_tool,
        params_obj,
        thresholds_obj,
        {"roi": roi},
    )
    diagnostics = getattr(runner, "last_diagnostics", {})
    runner.teardown()

    if tool_type == "locator.template_match":
        T_new = diagnostics.get("T") if isinstance(diagnostics, dict) else None
        context.T_total = compose_affine(context.T_total, T_new)

        apply_alignment = bool(params_dict.get("apply_alignment", True))
        if apply_alignment:
            source = context.frame_aligned if context.frame_aligned is not None else context.frame
            if source is None:
                raise ValueError("Aligned frame source not available")
            if isinstance(diagnostics, dict):
                T_new = diagnostics.get("T")
            else:
                T_new = None
            if T_new is None:
                context.frame_aligned = source
            else:
                T_inv = imaging.invert_affine(T_new)
                context.frame_aligned = imaging.warp_by_affine_u8(source, T_inv)
            context.frame_is_aligned = True
        else:
            context.frame_aligned = context.frame
            context.frame_is_aligned = False

    record_tool_latency(tool_stub.name or tool_type, float(result.latency_ms))
    return result


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

    import numpy as np

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


def _coerce_thresholds_dict(
    thresholds: Dict[str, Any] | ToolThresholds | None,
) -> Dict[str, Any]:
    if isinstance(thresholds, ToolThresholds):
        return dict(thresholds.values or {})
    return dict(thresholds or {})


def status_from_metrics(
    tool_type: str,
    metrics: Dict[str, Any] | None,
    thresholds: Dict[str, Any] | ToolThresholds | None,
) -> Literal["ok", "warn", "nok"]:
    """Map raw metric values to a normalized status for diagnostics."""

    tool_key = (tool_type or "").lower()
    metric_values = dict(metrics or {})
    threshold_values = _coerce_thresholds_dict(thresholds)

    if tool_key in {"locator.template_match", "template_match"}:
        corr = _safe_float(metric_values.get("corr"), 0.0)
        attempts = _safe_int(metric_values.get("match_attempts"), -1)
        corr_threshold = _safe_float(threshold_values.get("threshold_corr"), 0.55)
        if attempts == 0 and abs(corr) < 1e-6:
            return "warn"
        return "ok" if corr >= corr_threshold else "nok"

    if tool_key == "ssim":
        ssim_min = _safe_float(
            threshold_values.get("ssim_min"), DEFAULT_THRESHOLDS.get("ssim_min", 0.92)
        )
        ssim_val = _safe_float(metric_values.get("ssim"), 0.0)
        return "ok" if ssim_val >= ssim_min else "nok"

    if tool_key == "ssd":
        ssd_max = _safe_float(threshold_values.get("ssd_max"), 1.0e7)
        ssd_val = _safe_float(metric_values.get("ssd"), float("inf"))
        return "ok" if ssd_val <= ssd_max else "nok"

    if tool_key == "mse":
        mse_max = _safe_float(threshold_values.get("mse_max"), 25.0)
        mse_val = _safe_float(metric_values.get("mse"), float("inf"))
        return "ok" if mse_val <= mse_max else "nok"

    if tool_key == "ncc":
        ncc_min = _safe_float(threshold_values.get("ncc_min"), 0.9)
        ncc_val = _safe_float(metric_values.get("ncc"), -1.0)
        return "ok" if ncc_val >= ncc_min else "nok"

    if tool_key == "edge_change":
        edge_ratio_max = _safe_float(threshold_values.get("edge_ratio_max"), 0.05)
        edge_ratio = _safe_float(metric_values.get("edge_ratio"), 0.0)
        effective_pixels = metric_values.get("effective_pixels")
        if effective_pixels is not None and _safe_int(effective_pixels, 0) <= 0:
            return "warn"
        return "ok" if edge_ratio <= edge_ratio_max else "nok"

    if tool_key == "edge_profile_deviation":
        max_dev_max = _safe_float(threshold_values.get("max_deviation_max"), 0.1)
        coverage_min = _safe_float(threshold_values.get("coverage_min"), 0.6)
        max_dev = _safe_float(metric_values.get("max_deviation"), 0.0)
        coverage = _safe_float(metric_values.get("coverage"), 0.0)
        if coverage <= 0.0:
            return "warn"
        if max_dev > max_dev_max or coverage < coverage_min:
            return "nok"
        return "ok"

    if tool_key == "absdiff":
        blob_count = _safe_int(metric_values.get("blob_count"), 0)
        max_blob_count = _safe_int(
            threshold_values.get("max_blob_count"),
            DEFAULT_THRESHOLDS.get("max_blob_count", 10),
        )
        total_area = _safe_float(metric_values.get("total_area"), 0.0)
        max_total_area = _safe_float(
            threshold_values.get("max_total_area"),
            DEFAULT_THRESHOLDS.get("max_total_area", 2000.0),
        )
        if blob_count > max_blob_count or total_area > max_total_area:
            return "nok"
        return "ok"

    return "ok" if metric_values else "warn"


def _freeze_value(value: Any) -> Any:
    if isinstance(value, ToolRoi):
        rect = value.rect()
        return tuple(rect) if rect is not None else None
    if isinstance(value, ToolMask):
        mask = value.value
        if mask is None:
            return None
        return (
            mask.shape,
            mask.dtype.str,
            int(mask.__array_interface__["data"][0]),
        )
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, np.ndarray):
        return (
            value.shape,
            value.dtype.str,
            int(value.__array_interface__["data"][0]),
        )
    return value


def _freeze_dict(mapping: Dict[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if not mapping:
        return tuple()
    return tuple(sorted((str(k), _freeze_value(v)) for k, v in mapping.items()))


def _extract_translation_from_affine(T: np.ndarray | None) -> tuple[float, float]:
    """Return translation components from a 2×3 affine transform."""

    import numpy as np

    if T is None:
        return 0.0, 0.0

    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):  # pragma: no cover - defensive fallback
        return 0.0, 0.0

    return float(arr[0, 2]), float(arr[1, 2])


def _extract_rotation_from_affine(T: np.ndarray | None) -> float:
    """Return rotation angle in degrees encoded in a 2×3 affine transform."""

    if T is None:
        return 0.0

    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):  # pragma: no cover - defensive fallback
        return 0.0

    angle = math.degrees(math.atan2(float(arr[1, 0]), float(arr[0, 0])))
    return float(angle)


def run_locator_template_match(
    golden: np.ndarray,
    frame: np.ndarray,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    roi: Optional[ToolRoi | Dict[str, Any] | Sequence[int]],
    *,
    tool_id: str = "locator",
    cache: Optional[Dict[str, Any]] = None,
) -> Tuple[ToolRunResult, Dict[str, Any]]:
    """Run template matching locator core logic.

    Parameters
    ----------
    golden, frame:
        Golden reference and frame images. Only the first channel is used if
        images are multi-channel.
    params:
        Tool parameters or plain dictionary. Recognized keys are
        ``use_golden_crop`` (bool), ``template_roi`` (ROI descriptor),
        ``coarse_cap`` (int) and optional rotation settings
        ``rotation_enabled`` (bool), ``angle_range_deg`` (float),
        ``angle_step_deg`` (float), ``angle_enabled`` (bool),
        ``angle_roi`` (ROI descriptor), ``angle_method`` (str),
        ``angle_ref_deg`` (float), ``angle_max_dev_deg`` (float), and
        ``angle_smooth`` (float).
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

    import cv2
    import numpy as np
    from app.utils import imaging

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
    rotation_enabled = bool(params_dict.get("rotation_enabled", False))
    angle_enabled = bool(params_dict.get("angle_enabled", False))
    angle_range_deg = _safe_float(params_dict.get("angle_range_deg", 15.0), 15.0)
    angle_step_deg = _safe_float(params_dict.get("angle_step_deg", 1.0), 1.0)
    angle_method = str(params_dict.get("angle_method", "fitline")).strip().lower()
    angle_ref_deg = _safe_float(params_dict.get("angle_ref_deg", 0.0), 0.0)
    angle_max_dev_deg = _safe_float(params_dict.get("angle_max_dev_deg", 15.0), 15.0)
    angle_smooth = _safe_float(params_dict.get("angle_smooth", 0.0), 0.0)
    if not math.isfinite(angle_range_deg):
        angle_range_deg = 0.0
    if not math.isfinite(angle_step_deg) or abs(angle_step_deg) < 1e-6:
        angle_step_deg = 1.0
    angle_range_deg = max(0.0, abs(angle_range_deg))
    angle_step_deg = max(1e-3, abs(angle_step_deg))
    if not math.isfinite(angle_ref_deg):
        angle_ref_deg = 0.0
    if not math.isfinite(angle_max_dev_deg):
        angle_max_dev_deg = 0.0
    angle_max_dev_deg = max(0.0, abs(angle_max_dev_deg))
    if not math.isfinite(angle_smooth):
        angle_smooth = 0.0
    angle_smooth = max(0.0, min(1.0, angle_smooth))
    if angle_method not in {"fitline", "hough"}:
        angle_method = "fitline"

    timings: list[imaging.TimeBlockResult] = []
    dx = 0.0
    dy = 0.0
    corr = 0.0
    theta_deg = 0.0
    used = 0
    theta_raw: Optional[float] = None
    angle_roi_rect: Optional[tuple[int, int, int, int]] = None
    angle_fallback: Optional[str] = None

    def _normalize_angle_deg(angle: float) -> float:
        normalized = ((angle + 90.0) % 180.0) - 90.0
        return float(normalized)

    def estimate_angle_deg(frame_u8_crop: np.ndarray, method: str) -> Optional[float]:
        if frame_u8_crop.size == 0:
            return None
        edges = cv2.Canny(frame_u8_crop, 50, 150)
        points = np.column_stack(np.where(edges > 0))
        if points.shape[0] < 50:
            return None
        if method == "hough":
            h, w = edges.shape[:2]
            min_length = max(10, int(min(h, w) * 0.25))
            lines = cv2.HoughLinesP(
                edges,
                1,
                math.pi / 180.0,
                threshold=40,
                minLineLength=min_length,
                maxLineGap=10,
            )
            if lines is None:
                return None
            angles: list[float] = []
            for x1, y1, x2, y2 in lines[:, 0, :]:
                if x1 == x2 and y1 == y2:
                    continue
                angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
                angles.append(_normalize_angle_deg(angle))
            if not angles:
                return None
            return float(np.median(np.asarray(angles, dtype=np.float32)))

        points_xy = np.column_stack((points[:, 1], points[:, 0])).astype(np.float32)
        vx, vy, _x0, _y0 = cv2.fitLine(points_xy, cv2.DIST_L2, 0, 0.01, 0.01)
        angle = math.degrees(math.atan2(float(vy), float(vx)))
        return _normalize_angle_deg(angle)

    if angle_enabled:
        angle_roi_rect = _rect_from_any(params_dict.get("angle_roi"))
        angle_roi_rect = _clamp_rect(angle_roi_rect, frame_w, frame_h)
        if angle_roi_rect is None:
            angle_fallback = "missing_roi"
        else:
            ax, ay, aw, ah = angle_roi_rect
            if aw > 0 and ah > 0:
                crop = frame_u8[ay : ay + ah, ax : ax + aw]
                with imaging.time_block("angle_estimate", timings):
                    theta_raw = estimate_angle_deg(crop, angle_method)
            if theta_raw is None:
                angle_fallback = "estimate_failed"
            else:
                theta_deg = float(theta_raw) - float(angle_ref_deg)
                if abs(theta_deg) > angle_max_dev_deg:
                    theta_deg = 0.0
                    angle_fallback = "out_of_range"
                elif angle_smooth > 0.0 and cache is not None:
                    prev_theta = cache.get("angle_ema")
                    if isinstance(prev_theta, (int, float)) and math.isfinite(prev_theta):
                        theta_deg = (1.0 - angle_smooth) * float(prev_theta) + angle_smooth * theta_deg
                    cache["angle_ema"] = float(theta_deg)

    if template_rect is not None and search_rect is not None:
        tx, ty, tw, th = template_rect
        templ = golden_u8[ty : ty + th, tx : tx + tw]

        if templ.size > 0 and tw > 0 and th > 0:
            sx, sy, sw, sh = search_rect

            if angle_enabled:
                with imaging.time_block("match_template", timings):
                    dx_rel, dy_rel, corr_candidate, used_candidate = imaging.match_template_u8(
                        frame_u8,
                        templ,
                        roi=(sx, sy, sw, sh),
                        search_margin=0,
                        coarse_cap=int(max(1, coarse_cap)),
                        cache=cache,
                    )
                match_x = float(sx + dx_rel)
                match_y = float(sy + dy_rel)
                corr = float(corr_candidate)
                used = int(used_candidate)
                cg_x = tx + (tw / 2.0)
                cg_y = ty + (th / 2.0)
                cf_x = match_x + (tw / 2.0)
                cf_y = match_y + (th / 2.0)
                theta_rad = math.radians(theta_deg)
                cos_t = math.cos(theta_rad)
                sin_t = math.sin(theta_rad)
                dx = float(cf_x - (cos_t * cg_x - sin_t * cg_y))
                dy = float(cf_y - (sin_t * cg_x + cos_t * cg_y))
            else:
                def _enumerate_angles() -> list[float]:
                    if not rotation_enabled or angle_range_deg <= 1e-6:
                        return [0.0]
                    steps = int(math.floor(angle_range_deg / angle_step_deg + 1e-9))
                    candidates = [round(idx * angle_step_deg, 6) for idx in range(-steps, steps + 1)]
                    filtered = [
                        angle
                        for angle in candidates
                        if abs(angle) <= angle_range_deg + 1e-6
                    ]
                    if 0.0 not in filtered:
                        filtered.append(0.0)
                    return sorted(set(filtered))

                angle_candidates = _enumerate_angles()
                rotated_cache: Optional[Dict[Any, np.ndarray]] = None
                base_key: Optional[tuple[int, tuple[int, int], str]] = None
                if cache is not None:
                    rotated_cache = cache.setdefault("rotated_templates", {})
                    base_key = (
                        int(templ.__array_interface__["data"][0]),
                        templ.shape[:2],
                        templ.dtype.str,
                    )

                templates: list[tuple[float, np.ndarray]] = []
                max_w = tw
                max_h = th
                for angle in angle_candidates:
                    templ_variant: np.ndarray
                    if abs(angle) <= 1e-6:
                        templ_variant = templ
                    else:
                        cache_key = None
                        if rotated_cache is not None and base_key is not None:
                            cache_key = (base_key, round(angle, 4))
                            cached = rotated_cache.get(cache_key)
                            if cached is not None:
                                templ_variant = cached
                            else:
                                cache_key = (base_key, round(angle, 4))
                                center = (tw / 2.0, th / 2.0)
                                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                                cos_a = abs(M[0, 0])
                                sin_a = abs(M[0, 1])
                                new_w = max(1, int(math.ceil((th * sin_a) + (tw * cos_a))))
                                new_h = max(1, int(math.ceil((th * cos_a) + (tw * sin_a))))
                                M[0, 2] += (new_w / 2.0) - center[0]
                                M[1, 2] += (new_h / 2.0) - center[1]
                                templ_variant = cv2.warpAffine(
                                    templ,
                                    M,
                                    (new_w, new_h),
                                    flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REFLECT101,
                                )
                                rotated_cache[cache_key] = templ_variant
                        else:
                            center = (tw / 2.0, th / 2.0)
                            M = cv2.getRotationMatrix2D(center, angle, 1.0)
                            cos_a = abs(M[0, 0])
                            sin_a = abs(M[0, 1])
                            new_w = max(1, int(math.ceil((th * sin_a) + (tw * cos_a))))
                            new_h = max(1, int(math.ceil((th * cos_a) + (tw * sin_a))))
                            M[0, 2] += (new_w / 2.0) - center[0]
                            M[1, 2] += (new_h / 2.0) - center[1]
                            templ_variant = cv2.warpAffine(
                                templ,
                                M,
                                (new_w, new_h),
                                flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT101,
                            )
                    templates.append((angle, templ_variant))
                    max_w = max(max_w, templ_variant.shape[1])
                    max_h = max(max_h, templ_variant.shape[0])

                extra_margin = int(math.ceil(max(max_w - tw, max_h - th) / 2.0))

                best_corr = -2.0
                best_angle = 0.0
                best_match: Optional[tuple[float, float]] = None
                best_size = (tw, th)
                best_used = 0

                for angle, templ_variant in templates:
                    h_rot, w_rot = templ_variant.shape[:2]
                    available_w = sw + 2 * extra_margin
                    available_h = sh + 2 * extra_margin
                    if available_w < w_rot or available_h < h_rot:
                        continue
                    with imaging.time_block("match_template", timings):
                        dx_rel, dy_rel, corr_candidate, used_candidate = imaging.match_template_u8(
                            frame_u8,
                            templ_variant,
                            roi=(sx, sy, sw, sh),
                            search_margin=int(max(0, extra_margin)),
                            coarse_cap=int(max(1, coarse_cap)),
                            cache=cache,
                        )

                    match_x = float(sx + dx_rel)
                    match_y = float(sy + dy_rel)
                    if corr_candidate > best_corr:
                        best_corr = float(corr_candidate)
                        best_angle = float(angle)
                        best_match = (match_x, match_y)
                        best_size = (float(w_rot), float(h_rot))
                        best_used = int(used_candidate)

                if best_match is not None:
                    corr = float(best_corr)
                    used = int(best_used)
                    theta_deg = float(best_angle)
                    match_x, match_y = best_match
                    best_w, best_h = best_size
                    cg_x = tx + (tw / 2.0)
                    cg_y = ty + (th / 2.0)
                    cf_x = match_x + (best_w / 2.0)
                    cf_y = match_y + (best_h / 2.0)
                    theta_rad = math.radians(theta_deg)
                    cos_t = math.cos(theta_rad)
                    sin_t = math.sin(theta_rad)
                    dx = float(cf_x - (cos_t * cg_x - sin_t * cg_y))
                    dy = float(cf_y - (sin_t * cg_x + cos_t * cg_y))

    cos_theta = math.cos(math.radians(theta_deg))
    sin_theta = math.sin(math.radians(theta_deg))
    T = np.array([[cos_theta, -sin_theta, dx], [sin_theta, cos_theta, dy]], dtype=np.float32)

    found = bool(used > 0 and abs(float(corr)) > 1e-6)

    metrics = {
        "dx": float(dx),
        "dy": float(dy),
        "theta_deg": float(theta_deg),
        "corr": float(corr),
        "match_attempts": float(used),
        "found": found,
    }
    status = status_from_metrics("locator.template_match", metrics, thresholds_dict)

    diagnostics = {
        **metrics,
        "T": T,
        "status": status,
        "threshold_corr": _safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55),
    }
    if angle_enabled:
        diagnostics["theta_method"] = angle_method
        diagnostics["theta_raw"] = theta_raw
        if angle_roi_rect is not None:
            ax, ay, aw, ah = angle_roi_rect
            diagnostics["angle_roi"] = {"x": int(ax), "y": int(ay), "w": int(aw), "h": int(ah)}
        if angle_fallback:
            diagnostics["angle_fallback"] = angle_fallback
    if timings:
        diagnostics["timings_ms"] = {
            entry.name: float(entry.elapsed_ms) for entry in timings
        }

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    diagnostics["latency_ms"] = latency_ms
    result = ToolRunResult(
        status=status,
        metrics={**metrics, "latency_ms": float(latency_ms)},
        latency_ms=float(latency_ms),
        debug_artifacts={
            "tool_id": tool_id,
            "type": "locator.template_match",
            "diagnostics": diagnostics,
        },
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

    import numpy as np
    from app.utils import imaging

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
    timings: list[imaging.TimeBlockResult] = []

    roi_rect = _rect_from_any(roi)
    gh, gw = golden_u8.shape[:2]
    roi_rect = _clamp_rect(roi_rect, gw, gh)
    if roi_rect is None:
        roi_rect = (0, 0, gw, gh)

    dx_total, dy_total = _extract_translation_from_affine(T_total)
    virtual_alignment = False
    if not frame_is_aligned and (abs(dx_total) > 1e-3 or abs(dy_total) > 1e-3):
        with imaging.time_block("warp_alignment", timings):
            frame_u8 = imaging.warp_by_translation_u8(frame_orig_u8, -dx_total, -dy_total)
        virtual_alignment = True

    x, y, w, h = roi_rect
    golden_crop = golden_u8[y : y + h, x : x + w]
    frame_crop = frame_u8[y : y + h, x : x + w]

    with imaging.time_block("ssim", timings):
        ssim_val = float(imaging.ssim_u8(golden_crop, frame_crop))
    metrics = {"ssim": float(ssim_val)}
    status = status_from_metrics("ssim", metrics, thresholds_dict)
    diagnostics = {
        "ssim": ssim_val,
        "roi": {"x": x, "y": y, "w": w, "h": h},
        "virtual_alignment": virtual_alignment,
        "ssim_min": ssim_min,
        "dx_total": dx_total,
        "dy_total": dy_total,
    }
    if timings:
        diagnostics["timings_ms"] = {
            entry.name: float(entry.elapsed_ms) for entry in timings
        }

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    diagnostics["latency_ms"] = latency_ms

    result = ToolRunResult(
        status=status,
        metrics={**metrics, "latency_ms": float(latency_ms)},
        latency_ms=float(latency_ms),
        debug_artifacts={
            "tool_id": tool_id,
            "type": "ssim",
            "diagnostics": diagnostics,
        },
    )
    return result, diagnostics

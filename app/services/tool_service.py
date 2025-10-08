# app/services/tool_service.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

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
                    "required": True,
                },
            },
            default_thresholds={
                "tm_enable": {
                    "type": "bool",
                    "label": "enable_template_match",
                    "default": True,
                },
                "tm_margin": {
                    "type": "int",
                    "label": "search_margin",
                    "default": 200,
                    "min": 0,
                    "max": 2000,
                    "step": 10,
                },
                "tm_min_corr": {
                    "type": "float",
                    "label": "threshold_corr",
                    "default": 0.55,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
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
                "template_roi": None,
                "use_golden_crop": True,
                "coarse_to_fine": True,
                "coarse_cap": 600,
                "apply_alignment": True,
            },
            default_thresholds={
                "threshold_corr": 0.55,
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
                },
                "min_blob_area": {
                    "type": "int",
                    "label": "min_blob_area",
                    "default": 20,
                    "min": 0,
                    "max": 1_000_000,
                },
                "max_total_area": {
                    "type": "int",
                    "label": "max_total_area",
                    "default": 2000,
                    "min": 0,
                    "max": 10_000_000,
                },
                "max_blob_count": {
                    "type": "int",
                    "label": "max_blob_count",
                    "default": 10,
                    "min": 0,
                    "max": 10_000,
                },
            },
            category="Inspection",
        ),
    }

    @classmethod
    def list_tool_types(cls) -> List[str]:
        """Return available tool type identifiers."""

        return sorted(cls._TOOLS.keys())

    @classmethod
    def get_tool_meta(cls, tool_type: str) -> Optional[ToolMeta]:
        """Return metadata for a tool type if registered."""

        return cls._TOOLS.get(tool_type)

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

        diagnostics.append(diag_entry)

    return context, diagnostics, results


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
    result = ToolRunResult(
        tool_id=tool_id,
        type="locator.template_match",
        status=status,
        metrics=metrics,
    )
    return result, diagnostics


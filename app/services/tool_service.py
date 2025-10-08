# app/services/tool_service.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import imageio.v3 as iio
import numpy as np

from app.services.compare_service import analyze
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
    T_total: Optional[np.ndarray] = None


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


def run_pipeline_prepare(
    recipe: RecipeV2, golden: np.ndarray, frame: np.ndarray
) -> Tuple[ToolRunnerContext, List[Dict[str, Any]]]:
    """Iterate through tool pipeline and prepare shared context."""

    diagnostics: List[Dict[str, Any]] = []
    context = ToolRunnerContext(frame=frame, T_total=None)

    tools: Sequence[Tool] = sorted(recipe.tools, key=lambda t: t.order)

    for tool in tools:
        meta = ToolRegistry.get_tool_meta(tool.type)
        if meta is None:
            raise ValueError(f"Tool type '{tool.type}' is not registered")

        _validate_roi(tool, meta)
        _validate_ignore_mask(tool, meta)
        _validate_params(tool)

        diagnostics.append(
            {
                "tool_id": tool.name or f"tool_{tool.order}",
                "type": tool.type,
                "status": "skipped" if tool.enabled else "disabled",
            }
        )

    return context, diagnostics


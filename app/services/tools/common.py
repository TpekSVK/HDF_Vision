"""Shared helpers for low-level tool implementations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from app.models.schema import Tool, ToolParams, ToolThresholds
from app.services.tool_service import BaseTool, ToolRunResult
from app.services.tool_service import (
    _clamp_rect,
    _extract_translation_from_affine,
    _freeze_dict,
    _rect_from_any,
)
from app.services.tool_service import ToolRunnerContext  # type: ignore  # circular typing
from app.utils import imaging
from app.utils.imaging import TimeBlockResult


@dataclass(slots=True)
class PreparedPair:
    """Normalized grayscale ROI pair ready for metric computation."""

    golden_roi: np.ndarray
    frame_roi: np.ndarray
    roi_rect: Tuple[int, int, int, int]
    valid_mask: Optional[np.ndarray]
    dx_total: float
    dy_total: float
    virtual_alignment: bool

    @property
    def pixel_count(self) -> int:
        if self.valid_mask is not None:
            return int(self.valid_mask.sum())
        return int(self.golden_roi.size)


class PairTool(BaseTool):
    """Base helper encapsulating ROI, mask and alignment normalization."""

    _EPS = 1e-3

    def __init__(self) -> None:
        super().__init__()
        self._pair_cache_signature: tuple[Any, ...] | None = None
        self._roi_mask_cache_entry: Dict[str, Any] | None = None

    def prepare(self, context: Dict[str, Any]) -> None:  # type: ignore[override]
        super().prepare(context)
        self._pair_cache_signature = None
        self._roi_mask_cache_entry = None

    def teardown(self) -> None:  # type: ignore[override]
        super().teardown()
        self._pair_cache_signature = None
        self._roi_mask_cache_entry = None

    def _coerce_params_dict(self, params: ToolParams | Dict[str, Any] | None) -> Dict[str, Any]:
        if isinstance(params, ToolParams):
            return dict(params.values or {})
        return dict(params or {})

    def _coerce_thresholds_dict(
        self, thresholds: ToolThresholds | Dict[str, Any] | None
    ) -> Dict[str, Any]:
        if isinstance(thresholds, ToolThresholds):
            return dict(thresholds.values or {})
        return dict(thresholds or {})

    def _resolve_tool(self) -> Tool | None:
        tool = self._prepared_context.get("tool")
        return tool if isinstance(tool, Tool) else None

    def _resolve_runner_context(self) -> ToolRunnerContext:
        runner_context = self._prepared_context.get("runner_context")
        if runner_context is None:
            raise ValueError("Runner context missing for tool execution")
        return runner_context

    def _ensure_pair_cache(
        self,
        frame: np.ndarray,
        params_dict: Dict[str, Any],
        thresholds_dict: Dict[str, Any],
    ) -> None:
        frame_shape = tuple(np.asarray(frame).shape[:2])
        signature = (frame_shape, _freeze_dict(params_dict), _freeze_dict(thresholds_dict))
        if signature != self._pair_cache_signature:
            self._pair_cache_signature = signature
            self._roi_mask_cache_entry = None

    def _resolve_cached_mask(
        self,
        mask_full: np.ndarray,
        roi_rect: Tuple[int, int, int, int],
        roi_shape: Tuple[int, int],
        dx_total: float,
        dy_total: float,
    ) -> Optional[np.ndarray]:
        key = (
            int(mask_full.__array_interface__["data"][0]),
            mask_full.shape,
            mask_full.dtype.str,
            roi_rect,
            round(dx_total, 4),
            round(dy_total, 4),
        )
        cached = self._roi_mask_cache_entry
        if cached and cached.get("key") == key:
            return cached.get("mask")

        x, y, w, h = roi_rect
        mask_roi = mask_full[y : y + h, x : x + w]
        if mask_roi.shape != roi_shape:
            mask = None
        else:
            mask = mask_roi == 0

        self._roi_mask_cache_entry = {"key": key, "mask": mask}
        return mask

    def _prepare_pair(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        roi_context: Dict[str, Any] | None,
    ) -> PreparedPair:
        tool = self._resolve_tool()
        runner_context = self._resolve_runner_context()

        golden_u8 = imaging.to_gray_u8(np.asarray(golden))
        frame_in_u8 = imaging.to_gray_u8(np.asarray(frame))

        frame_source = frame_in_u8
        dx_total = 0.0
        dy_total = 0.0
        virtual_alignment = False

        if runner_context is not None:
            dx_total, dy_total = _extract_translation_from_affine(runner_context.T_total)
            if runner_context.frame_is_aligned:
                aligned = runner_context.frame_aligned
                if aligned is not None:
                    frame_source = imaging.to_gray_u8(np.asarray(aligned))
                else:
                    frame_source = frame_in_u8
            else:
                aligned = runner_context.frame_aligned
                if aligned is not None:
                    frame_source = imaging.to_gray_u8(np.asarray(aligned))
                elif (abs(dx_total) > self._EPS or abs(dy_total) > self._EPS) and runner_context.frame is not None:
                    frame_source = imaging.warp_by_translation_u8(
                        imaging.to_gray_u8(np.asarray(runner_context.frame)),
                        -dx_total,
                        -dy_total,
                    )
                    virtual_alignment = True
                else:
                    frame_source = frame_in_u8

        gh, gw = golden_u8.shape[:2]
        roi_candidate: Any = None
        if tool is not None and tool.roi.rect() is not None:
            roi_candidate = tool.roi
        elif roi_context is not None:
            roi_candidate = roi_context.get("roi")
        roi_rect = _clamp_rect(_rect_from_any(roi_candidate), gw, gh)
        if roi_rect is None:
            roi_rect = (0, 0, gw, gh)

        x, y, w, h = roi_rect
        if w <= 0 or h <= 0:
            raise ValueError("ROI has zero area")

        golden_roi = golden_u8[y : y + h, x : x + w]
        frame_roi = frame_source[y : y + h, x : x + w]

        mask = None
        if tool is not None and tool.ignore_mask.value is not None:
            mask_full = np.asarray(tool.ignore_mask.value, dtype=np.uint8)
            if mask_full.shape[:2] != (gh, gw):
                mask_full = mask_full[:gh, :gw]
            mask = self._resolve_cached_mask(
                mask_full,
                roi_rect,
                (h, w),
                dx_total,
                dy_total,
            )

        return PreparedPair(
            golden_roi=golden_roi,
            frame_roi=frame_roi,
            roi_rect=roi_rect,
            valid_mask=mask,
            dx_total=float(dx_total),
            dy_total=float(dy_total),
            virtual_alignment=virtual_alignment,
        )

    def _finalize_result(
        self,
        status: str,
        metrics: Dict[str, float],
        diagnostics: Dict[str, Any],
        latency_ms: float,
        tool_id: str,
        debug_type: str,
        timings: Sequence[TimeBlockResult] | None = None,
    ) -> ToolRunResult:
        payload = {
            "tool_id": tool_id,
            "type": debug_type,
            "diagnostics": {**diagnostics, "latency_ms": latency_ms},
        }
        if timings:
            payload["diagnostics"]["timings_ms"] = {
                entry.name: float(entry.elapsed_ms) for entry in timings
            }
        return ToolRunResult(
            status=status,
            metrics={**metrics, "latency_ms": float(latency_ms)},
            latency_ms=float(latency_ms),
            debug_artifacts=payload,
        )

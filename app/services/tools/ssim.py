"""Structural similarity implementation."""
from __future__ import annotations
from typing import Any, Dict, Optional, Sequence, Tuple
import time
import numpy as np
from app.services.roi_geometry import roi_shape_mask
from app.models.schema import Tool, ToolParams, ToolRoi, ToolThresholds
from app.services.tool_contracts import BaseTool, ToolRunResult, ToolRunnerContext
from app.services.tool_status import DEFAULT_SSIM_MIN, status_from_metrics
from app.services.tool_image_helpers import _clamp_rect, _ensure_gray_u8, _extract_translation_from_affine, _rect_from_any


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

        ignore_mask = (
            tool.ignore_mask.value
            if isinstance(tool, Tool) and tool.ignore_mask.value is not None
            else None
        )

        result, diagnostics = run_ssim_tool(
            golden,
            frame,
            thresholds_obj,
            roi,
            frame_original=frame_original,
            T_total=T_total,
            frame_is_aligned=frame_is_aligned,
            tool_id=str(tool_id),
            ignore_mask=ignore_mask,
            preview_sink=(lambda value: setattr(self, "filtered_roi", value))
            if self._prepared_context.get("capture_filtered_roi") else None,
        )
        self.last_diagnostics = diagnostics
        return result


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
    ignore_mask: np.ndarray | None = None,
    preview_sink=None,
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
    ssim_min = float(thresholds_dict.get("ssim_min", DEFAULT_SSIM_MIN))
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

    mask_note: str | None = None
    shape_mask = roi_shape_mask(roi, roi_rect)
    include_mask_crop: np.ndarray | None = (
        shape_mask.astype(np.uint8) * 255 if shape_mask is not None else None
    )
    ignore_mask_pixels = 0
    effective_mask_pixels = (
        int(np.count_nonzero(include_mask_crop))
        if include_mask_crop is not None else int(w * h)
    )
    if ignore_mask is not None:
        try:
            with imaging.time_block("prepare_ignore_mask", timings):
                mask_arr = np.asarray(ignore_mask)
                if mask_arr.ndim > 2:
                    mask_arr = np.squeeze(mask_arr)
                if mask_arr.ndim != 2:
                    raise ValueError(f"expected 2D mask, got ndim={mask_arr.ndim}")

                mask_shape = tuple(mask_arr.shape[:2])
                ignore_mask_crop_bool: np.ndarray | None = None

                if mask_shape == (gh, gw):
                    ignore_mask_crop_bool = mask_arr[y : y + h, x : x + w] > 0
                    mask_note = "ignore_mask_space=global"
                elif mask_shape == (h, w):
                    ignore_mask_crop_bool = mask_arr > 0
                    mask_note = "ignore_mask_space=roi_local"
                else:
                    overlap_h = min(h, mask_arr.shape[0])
                    overlap_w = min(w, mask_arr.shape[1])
                    if overlap_h > 0 and overlap_w > 0:
                        ignore_mask_crop_bool = mask_arr[:overlap_h, :overlap_w] > 0
                        mask_note = (
                            "ignore_mask_space=fallback_roi_local_overlap:"
                            f"mask_shape={mask_shape},overlap={(overlap_h, overlap_w)}"
                        )
                    else:
                        mask_note = (
                            "ignore_mask_ignored:no_roi_overlap:"
                            f"mask_shape={mask_shape},roi_shape={(h, w)}"
                        )

                if ignore_mask_crop_bool is not None:
                    if include_mask_crop is None:
                        include_mask_crop = np.ones((h, w), dtype=np.uint8) * 255
                    crop_h, crop_w = ignore_mask_crop_bool.shape[:2]
                    roi_view = include_mask_crop[:crop_h, :crop_w]
                    roi_view[ignore_mask_crop_bool] = 0
                    ignore_mask_pixels = int(np.count_nonzero(ignore_mask_crop_bool))
                    effective_mask_pixels = int(np.count_nonzero(include_mask_crop))
        except Exception as exc:
            include_mask_crop = shape_mask.astype(np.uint8) * 255 if shape_mask is not None else None
            ignore_mask_pixels = 0
            effective_mask_pixels = (
                int(np.count_nonzero(include_mask_crop))
                if include_mask_crop is not None else int(w * h)
            )
            mask_note = f"ignore_mask_ignored:{exc}"

    if include_mask_crop is not None and effective_mask_pixels > 0:
        frame_crop = frame_crop.copy()
        frame_crop[include_mask_crop == 0] = golden_crop[include_mask_crop == 0]

    if preview_sink is not None:
        preview_sink({
            "image": frame_crop.copy(), "rect": roi_rect,
            "mask": include_mask_crop != 0 if include_mask_crop is not None else np.ones((h, w), bool),
            "label": "Bez predbežného filtrovania · vstup SSIM",
            "to_display": np.array([[1, 0, dx_total], [0, 1, dy_total]], np.float32) if virtual_alignment else None,
        })

    with imaging.time_block("ssim", timings):
        ssim_val = (
            float(imaging.ssim_u8(golden_crop, frame_crop, mask_u8=include_mask_crop))
            if effective_mask_pixels > 0 else 0.0
        )
    metrics = {"ssim": float(ssim_val)}
    status = (
        status_from_metrics("ssim", metrics, thresholds_dict)
        if effective_mask_pixels > 0 else "warn"
    )
    diagnostics = {
        "ssim": ssim_val,
        "roi": {"x": x, "y": y, "w": w, "h": h},
        "virtual_alignment": virtual_alignment,
        "ssim_min": ssim_min,
        "dx_total": dx_total,
        "dy_total": dy_total,
        "roi_shape": (
            ToolRoi.from_obj(roi).shape()
            if isinstance(roi, (ToolRoi, dict)) else "rect"
        ),
        "shape_mask_used": shape_mask is not None,
        "ignore_mask_used": ignore_mask is not None and ignore_mask_pixels > 0,
        "ignore_mask_pixels": int(ignore_mask_pixels),
        "effective_mask_pixels": int(effective_mask_pixels),
    }
    if effective_mask_pixels == 0:
        diagnostics["mask_note"] = "effective_mask_area=0"
    elif mask_note is not None:
        diagnostics["mask_note"] = mask_note
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

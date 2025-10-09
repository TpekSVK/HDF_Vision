from __future__ import annotations

import time
from typing import Any, Dict

import cv2
import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging


class EdgeChangeTool(PairTool):
    """Detect edge-like differences via thresholded absolute difference."""

    def run(  # type: ignore[override]
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: Dict[str, Any],
    ) -> ToolRunResult:
        start = time.perf_counter()

        params_dict = self._coerce_params_dict(params)
        thresholds_dict = self._coerce_thresholds_dict(thresholds)

        self._ensure_pair_cache(frame, params_dict, thresholds_dict)
        prepared = self._prepare_pair(golden, frame, context)

        sigma = max(0.0, float(params_dict.get("blur_sigma", 1.0)))
        diff_threshold = float(params_dict.get("diff_threshold", 25.0))
        diff_threshold = max(0.0, min(diff_threshold, 255.0))
        use_morph = bool(params_dict.get("use_morphology", False))
        morph_open = int(params_dict.get("morph_open", 3))
        morph_dilate = int(params_dict.get("morph_dilate", 3))

        golden_roi = prepared.golden_roi
        frame_roi = prepared.frame_roi

        if sigma > 1e-6:
            golden_roi = imaging.blur_gaussian_u8(golden_roi, sigma)
            frame_roi = imaging.blur_gaussian_u8(frame_roi, sigma)

        diff = imaging.absdiff_u8(golden_roi, frame_roi)
        binary = imaging.threshold_bin_u8(diff, diff_threshold, typ=cv2.THRESH_BINARY)

        if use_morph:
            binary = imaging.morphology_open_then_dilate_u8(binary, k_open=max(1, morph_open), k_dil=max(1, morph_dilate))

        diff_float = diff.astype(np.float32)

        if prepared.valid_mask is not None:
            valid_mask = prepared.valid_mask
            diff_values = diff_float[valid_mask]
            binary_values = binary[valid_mask]
        else:
            diff_values = diff_float.reshape(-1)
            binary_values = binary.reshape(-1)

        effective_pixels = int(diff_values.size)
        changed_pixels = int(np.count_nonzero(binary_values))
        edge_ratio = float(changed_pixels / effective_pixels) if effective_pixels > 0 else 0.0
        mean_diff = float(np.mean(diff_values, dtype=np.float32)) if effective_pixels > 0 else 0.0

        edge_ratio_max = float(thresholds_dict.get("edge_ratio_max", 0.05))
        status = "ok" if effective_pixels > 0 and edge_ratio <= edge_ratio_max else ("warn" if effective_pixels == 0 else "nok")

        latency_ms = (time.perf_counter() - start) * 1000.0

        diagnostics = {
            "roi": {
                "x": int(prepared.roi_rect[0]),
                "y": int(prepared.roi_rect[1]),
                "w": int(prepared.roi_rect[2]),
                "h": int(prepared.roi_rect[3]),
            },
            "dx_total": prepared.dx_total,
            "dy_total": prepared.dy_total,
            "virtual_alignment": prepared.virtual_alignment,
            "effective_pixels": effective_pixels,
            "changed_pixels": changed_pixels,
            "sigma": sigma,
            "diff_threshold": diff_threshold,
            "edge_ratio_max": edge_ratio_max,
            "use_morphology": use_morph,
            "morph_open": morph_open,
            "morph_dilate": morph_dilate,
        }

        metrics = {
            "edge_ratio": float(round(edge_ratio, 5)),
            "mean_diff": float(round(mean_diff, 5)),
        }

        return self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=self._prepared_context.get("tool_id", "edge_change"),
            debug_type="edge_change",
        )

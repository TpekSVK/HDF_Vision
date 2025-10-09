from __future__ import annotations

import math
import time
from typing import Any, Dict

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging


class MSETool(PairTool):
    """Compute Mean Squared Error within ROI."""

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

        prepared = self._prepare_pair(golden, frame, context)
        sigma = float(params_dict.get("preblur_sigma", 0.0))
        sigma = max(0.0, float(sigma))

        golden_roi = prepared.golden_roi
        frame_roi = prepared.frame_roi

        if sigma > 1e-6:
            golden_roi = imaging.blur_gaussian_u8(golden_roi, sigma)
            frame_roi = imaging.blur_gaussian_u8(frame_roi, sigma)

        diff_abs = imaging.absdiff_u8(golden_roi, frame_roi).astype(np.float32)
        if prepared.valid_mask is not None:
            valid_values = diff_abs[prepared.valid_mask]
        else:
            valid_values = diff_abs.reshape(-1)

        effective_pixels = int(valid_values.size)
        latency_ms = (time.perf_counter() - start) * 1000.0

        if effective_pixels == 0:
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
                "effective_pixels": 0,
                "sigma": sigma,
            }
            return self._finalize_result(
                status="warn",
                metrics={"mse": 0.0, "rmse": 0.0},
                diagnostics=diagnostics,
                latency_ms=latency_ms,
                tool_id=self._prepared_context.get("tool_id", "mse"),
                debug_type="mse",
            )

        squared = valid_values * valid_values
        mse_value = float(np.mean(squared, dtype=np.float32))
        rmse_value = float(math.sqrt(max(mse_value, 0.0)))

        mse_max = float(thresholds_dict.get("mse_max", 25.0))
        status = "ok" if mse_value <= mse_max else "nok"

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
            "sigma": sigma,
            "mse_max": mse_max,
        }

        metrics = {
            "mse": float(round(mse_value, 5)),
            "rmse": float(round(rmse_value, 5)),
        }

        return self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=self._prepared_context.get("tool_id", "mse"),
            debug_type="mse",
        )

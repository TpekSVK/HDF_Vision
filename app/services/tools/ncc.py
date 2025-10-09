from __future__ import annotations

import time
from typing import Any, Dict, List

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging
from app.utils.imaging import TimeBlockResult, time_block


class NCCTool(PairTool):
    """Normalized cross-correlation between golden and frame ROI."""

    def run(  # type: ignore[override]
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: Dict[str, Any],
    ) -> ToolRunResult:
        start = time.perf_counter()
        timings: List[TimeBlockResult] = []

        params_dict = self._coerce_params_dict(params)
        thresholds_dict = self._coerce_thresholds_dict(thresholds)

        with time_block("prepare_pair", timings):
            self._ensure_pair_cache(frame, params_dict, thresholds_dict)
            prepared = self._prepare_pair(golden, frame, context)
        sigma = float(params_dict.get("preblur_sigma", 0.0))
        sigma = max(0.0, float(sigma))

        golden_roi = prepared.golden_roi
        frame_roi = prepared.frame_roi

        if sigma > 1e-6:
            with time_block("blur", timings):
                golden_roi = imaging.blur_gaussian_u8(golden_roi, sigma)
                frame_roi = imaging.blur_gaussian_u8(frame_roi, sigma)

        with time_block("astype", timings):
            gold_f = golden_roi.astype(np.float32)
            frame_f = frame_roi.astype(np.float32)

        if prepared.valid_mask is not None:
            valid = prepared.valid_mask
            with time_block("mask_select", timings):
                gold_vec = gold_f[valid]
                frame_vec = frame_f[valid]
        else:
            with time_block("flatten", timings):
                gold_vec = gold_f.reshape(-1)
                frame_vec = frame_f.reshape(-1)

        effective_pixels = int(gold_vec.size)
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
                metrics={"ncc": 0.0},
                diagnostics=diagnostics,
                latency_ms=latency_ms,
                tool_id=self._prepared_context.get("tool_id", "ncc"),
                debug_type="ncc",
                timings=timings,
            )

        with time_block("center", timings):
            gold_centered = gold_vec - float(np.mean(gold_vec, dtype=np.float32))
            frame_centered = frame_vec - float(np.mean(frame_vec, dtype=np.float32))

        with time_block("norm", timings):
            denom = float(np.linalg.norm(gold_centered) * np.linalg.norm(frame_centered))
        if denom <= 1e-6:
            with time_block("allclose", timings):
                ncc_value = 1.0 if np.allclose(gold_vec, frame_vec) else 0.0
        else:
            with time_block("dot", timings):
                ncc_value = float(np.dot(gold_centered, frame_centered) / denom)

        ncc_min = float(thresholds_dict.get("ncc_min", 0.9))
        status = "ok" if ncc_value >= ncc_min else "nok"

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
            "ncc_min": ncc_min,
        }

        metrics = {"ncc": float(round(ncc_value, 5))}

        return self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=self._prepared_context.get("tool_id", "ncc"),
            debug_type="ncc",
            timings=timings,
        )

"""Experimental statistical presence/absence V2 tool."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.presence_absence_v2_service import (
    compute_roi_hash,
    evaluate_sample,
    load_model,
    sensitivity_to_score_threshold,
)
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool


class PresenceAbsenceV2Tool(PairTool):
    def run(self, *args: Any, **kwargs: Any) -> ToolRunResult:  # type: ignore[override]
        if len(args) < 4:
            raise TypeError("Unsupported arguments for PresenceAbsenceV2Tool.run()")
        golden, frame, params, thresholds = args[:4]
        context = args[4] if len(args) > 4 else kwargs.get("context", {})
        return self._run_pipeline(
            np.asarray(golden),
            np.asarray(frame),
            params if isinstance(params, ToolParams) else ToolParams.from_obj(params),
            thresholds if isinstance(thresholds, ToolThresholds) else ToolThresholds.from_obj(thresholds),
            context if isinstance(context, dict) else {},
        )

    def _run_pipeline(
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

        model_ready = bool(params_dict.get("reference_model_ready", False))
        assets_dir = Path(str(params_dict.get("reference_assets_dir", "") or ""))
        model = load_model(assets_dir / "model") if assets_dir else None
        tool = self._resolve_tool()
        if model_ready and model is not None and tool is not None:
            current_hash = compute_roi_hash(tool.roi, tool.ignore_mask.value)
            model_hash = str(model.stats.get("roi_hash", "") or "")
            configured_hash = str(params_dict.get("roi_hash", "") or "")
            if (model_hash and model_hash != current_hash) or (
                    configured_hash and configured_hash != current_hash):
                model_ready = False

        if not model_ready or model is None:
            latency_ms = (time.perf_counter() - start) * 1000.0
            valid_pixels = prepared.pixel_count
            total_pixels = int(prepared.frame_roi.size)
            metrics = {
                "model_ready": False,
                "ok_sample_count": int(params_dict.get("sample_count_ok", 0) or 0),
                "nok_sample_count": int(params_dict.get("sample_count_nok", 0) or 0),
                "anomaly_score": 0.0,
                "anomaly_area": 0.0,
                "anomaly_area_percent": 0.0,
                "blob_count": 0,
                "largest_blob_area": 0,
                "blobs": [],
                "valid_pixel_count": valid_pixels,
                "ignored_pixel_count": max(0, total_pixels - valid_pixels),
                "fail_area_px": False,
                "fail_area_percent": False,
                "fail_largest_blob": False,
                "fail_blob_count": False,
                "decision_reason": "",
                "max_deviation": 0.0,
                "mean_deviation": 0.0,
                "latency_ms": float(latency_ms),
            }
            return ToolRunResult(
                status="warn",
                metrics=metrics,
                latency_ms=float(latency_ms),
                debug_artifacts={
                    "type": "presence_absence_v2",
                    "diagnostics": {
                        "message": "Model nie je pripravený. Najprv vykonajte učenie.",
                        "model_ready": False,
                    },
                    "preview": {"current_sample": prepared.frame_roi},
                },
            )

        score_threshold = (
            sensitivity_to_score_threshold(thresholds_dict["sensitivity"])
            if "sensitivity" in thresholds_dict
            else float(thresholds_dict.get("score_threshold", 4.0) or 4.0)
        )
        result = evaluate_sample(
            prepared.frame_roi,
            model.median,
            model.mad,
            polarity=str(params_dict.get("polarity", "any") or "any"),
            score_threshold=score_threshold,
            total_area_threshold=float(thresholds_dict.get("total_area_threshold", 50.0) or 50.0),
            min_blob_area=float(thresholds_dict.get("min_blob_area", 10.0) or 10.0),
            ignore_mask=(
                np.logical_not(prepared.valid_mask)
                if prepared.valid_mask is not None else None
            ),
            max_blob_count=int(thresholds_dict.get("max_blob_count", 0) or 0),
            max_largest_blob_area=float(
                thresholds_dict.get("max_largest_blob_area", 0.0) or 0.0
            ),
            max_anomaly_area_percent=float(
                thresholds_dict.get("max_anomaly_area_percent", 0.0) or 0.0
            ),
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        roi_x, roi_y, _, _ = prepared.roi_rect
        blobs = [
            {
                **blob,
                "image_x": roi_x + int(blob["x"]),
                "image_y": roi_y + int(blob["y"]),
            }
            for blob in result["blobs"]
        ]
        metrics = {
            "anomaly_score": float(result["anomaly_score"]),
            "anomaly_area": float(result["anomaly_area"]),
            "anomaly_area_percent": float(result["anomaly_area_percent"]),
            "blob_count": int(result["blob_count"]),
            "largest_blob_area": int(result["largest_blob_area"]),
            "blobs": blobs,
            "valid_pixel_count": int(result["valid_pixel_count"]),
            "ignored_pixel_count": int(result["ignored_pixel_count"]),
            "fail_area_px": bool(result["fail_area_px"]),
            "fail_area_percent": bool(result["fail_area_percent"]),
            "fail_largest_blob": bool(result["fail_largest_blob"]),
            "fail_blob_count": bool(result["fail_blob_count"]),
            "decision_reason": str(result["decision_reason"]),
            "max_deviation": float(result["max_deviation"]),
            "mean_deviation": float(result["mean_deviation"]),
            "model_ready": True,
            "ok_sample_count": int(params_dict.get("sample_count_ok", model.stats.get("sample_count_ok", 0)) or 0),
            "nok_sample_count": int(params_dict.get("sample_count_nok", model.stats.get("sample_count_nok", 0)) or 0),
            "latency_ms": float(latency_ms),
        }
        diagnostics = dict(metrics)
        if int(result["valid_pixel_count"]) == 0:
            diagnostics["message"] = "Ignore Mask zakrýva celú oblasť kontroly."
        display_items = [
            {
                "kind": "rect",
                "rect": (
                    roi_x + int(blob["x"]),
                    roi_y + int(blob["y"]),
                    int(blob["width"]),
                    int(blob["height"]),
                ),
                "color": "#ef4444",
                "thickness": 3,
                "label": f"Anomália {index}",
                "z_index": 40,
            }
            for index, blob in enumerate(blobs, start=1)
        ] if result["status"] == "nok" else []
        return ToolRunResult(
            status=str(result["status"]),
            metrics=metrics,
            latency_ms=float(latency_ms),
            debug_artifacts={
                "type": "presence_absence_v2",
                "diagnostics": diagnostics,
                "display_items": display_items,
                "preview": {
                    "current_sample": prepared.frame_roi,
                    "median_image": np.clip(model.median, 0, 255).astype(np.uint8),
                    "diff_map": result["diff_map"],
                    "binary_mask": result["binary_mask"],
                    "overlay": result["overlay"],
                },
            },
        )

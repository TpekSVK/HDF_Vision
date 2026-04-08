"""Experimental statistical presence/absence V2 tool."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.presence_absence_v2_service import evaluate_sample, load_model
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

        if not model_ready or model is None:
            latency_ms = (time.perf_counter() - start) * 1000.0
            metrics = {
                "model_ready": False,
                "ok_sample_count": int(params_dict.get("sample_count_ok", 0) or 0),
                "nok_sample_count": int(params_dict.get("sample_count_nok", 0) or 0),
                "anomaly_score": 0.0,
                "anomaly_area": 0.0,
                "blob_count": 0,
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

        result = evaluate_sample(
            prepared.frame_roi,
            model.median,
            model.mad,
            polarity=str(params_dict.get("polarity", "any") or "any"),
            score_threshold=float(thresholds_dict.get("score_threshold", 4.0) or 4.0),
            total_area_threshold=float(thresholds_dict.get("total_area_threshold", 50.0) or 50.0),
            min_blob_area=float(thresholds_dict.get("min_blob_area", 10.0) or 10.0),
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        metrics = {
            "anomaly_score": float(result["anomaly_score"]),
            "anomaly_area": float(result["anomaly_area"]),
            "blob_count": int(result["blob_count"]),
            "max_deviation": float(result["max_deviation"]),
            "mean_deviation": float(result["mean_deviation"]),
            "model_ready": True,
            "ok_sample_count": int(params_dict.get("sample_count_ok", model.stats.get("sample_count_ok", 0)) or 0),
            "nok_sample_count": int(params_dict.get("sample_count_nok", model.stats.get("sample_count_nok", 0)) or 0),
            "latency_ms": float(latency_ms),
        }
        return ToolRunResult(
            status=str(result["status"]),
            metrics=metrics,
            latency_ms=float(latency_ms),
            debug_artifacts={
                "type": "presence_absence_v2",
                "diagnostics": metrics,
                "preview": {
                    "current_sample": prepared.frame_roi,
                    "median_image": np.clip(model.median, 0, 255).astype(np.uint8),
                    "diff_map": result["diff_map"],
                    "binary_mask": result["binary_mask"],
                    "overlay": result["overlay"],
                },
            },
        )

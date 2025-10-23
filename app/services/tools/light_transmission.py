"""Light transmission tool measuring grayscale statistics in ROI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Sequence

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging
from app.utils.imaging import TimeBlockResult, time_block


@dataclass(slots=True)
class LightTransmissionCheckParams:
    """Configuration parameters for the light transmission tool."""

    name: str = "Light Transmission Check"
    calibration_enabled: bool = False
    calibration_dark_gray: float = 0.0
    calibration_bright_gray: float = 255.0
    threshold_value: float = 128.0


@dataclass(slots=True)
class ToolContext:
    """Runtime context for the lightweight API."""

    params: LightTransmissionCheckParams = field(default_factory=LightTransmissionCheckParams)
    thresholds: Dict[str, Any] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """Result returned by the lightweight API."""

    ok: bool
    metrics: Dict[str, Any]
    reason: str | None = None


class LightTransmissionCheckTool(PairTool):
    """Measure grayscale statistics and light transmission of an ROI."""

    TOOL_ID = "light_transmission"

    def run(self, *args: Any, **kwargs: Any) -> ToolResult | ToolRunResult:  # type: ignore[override]
        if len(args) == 3 and not kwargs:
            image, roi_mask, context = args
            return self._run_lightweight(
                np.asarray(image),
                np.asarray(roi_mask) if roi_mask is not None else None,
                context if isinstance(context, ToolContext) else ToolContext(),
            )

        if len(args) >= 4:
            golden, frame, params, thresholds = args[:4]
            context = args[4] if len(args) > 4 else kwargs.get("context", {})
            return self._run_pipeline(
                np.asarray(golden),
                np.asarray(frame),
                params if isinstance(params, ToolParams) else ToolParams.from_obj(params),
                thresholds
                if isinstance(thresholds, ToolThresholds)
                else ToolThresholds.from_obj(thresholds),
                context if isinstance(context, dict) else {},
            )

        raise TypeError("Unsupported arguments for LightTransmissionCheckTool.run()")

    # ------------------------------------------------------------------
    # Lightweight API ---------------------------------------------------
    # ------------------------------------------------------------------
    def _run_lightweight(
        self,
        image: np.ndarray,
        roi_mask: np.ndarray | None,
        context: ToolContext,
    ) -> ToolResult:
        params = self._sanitize_params(context.params)
        thresholds = self._sanitize_thresholds(context.thresholds)

        image_u8 = imaging.to_gray_u8(np.asarray(image))
        mask_bool = self._sanitize_mask(roi_mask, image_u8.shape[:2])

        metrics = self._compute_metrics(image_u8, mask_bool, params)
        ok, reason = self._evaluate_thresholds(metrics, params, thresholds)
        if reason is not None:
            metrics.setdefault("reason", reason)

        return ToolResult(ok=ok, metrics=metrics, reason=reason)

    # ------------------------------------------------------------------
    # Pipeline integration ---------------------------------------------
    # ------------------------------------------------------------------
    def _run_pipeline(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: Dict[str, Any],
    ) -> ToolRunResult:
        start = time.perf_counter()
        timings: list[TimeBlockResult] = []

        params_dict = self._coerce_params_dict(params)
        thresholds_dict = self._coerce_thresholds_dict(thresholds)

        with time_block("prepare_pair", timings):
            self._ensure_pair_cache(frame, params_dict, thresholds_dict)
            prepared = self._prepare_pair(golden, frame, context)

        roi_frame = prepared.frame_roi
        roi_mask = self._mask_from_prepared(prepared)
        params_obj = self._params_from_mapping(params_dict)
        thresholds_obj = self._sanitize_thresholds(thresholds_dict)

        lightweight_context = ToolContext(params=params_obj, thresholds=thresholds_obj)
        light_result = self._run_lightweight(roi_frame, roi_mask, lightweight_context)

        latency_ms = (time.perf_counter() - start) * 1000.0
        status = "ok" if light_result.ok else "nok"

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
            "calibration_enabled": params_obj.calibration_enabled,
            "calibration_dark_gray": float(params_obj.calibration_dark_gray),
            "calibration_bright_gray": float(params_obj.calibration_bright_gray),
            "threshold_value": float(params_obj.threshold_value),
        }

        if light_result.reason is not None:
            diagnostics["reason"] = light_result.reason

        metrics = dict(light_result.metrics)

        result = self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=self._prepared_context.get("tool_id", self.TOOL_ID),
            debug_type=self.TOOL_ID,
            timings=timings,
        )

        artifacts = result.debug_artifacts
        if isinstance(artifacts, dict):
            diagnostics_payload = artifacts.setdefault("diagnostics", {})
            diagnostics_payload.setdefault("reason", light_result.reason)
            diagnostics_payload.setdefault("mean_gray", metrics.get("mean_gray"))
            diagnostics_payload.setdefault("median_gray", metrics.get("median_gray"))
            diagnostics_payload.setdefault("pct_above_T", metrics.get("pct_above_T"))
            diagnostics_payload.setdefault("pct_below_T", metrics.get("pct_below_T"))

            preview = artifacts.setdefault("preview", {})
            preview.setdefault("frame", roi_frame)

        self.last_diagnostics = diagnostics
        return result

    # ------------------------------------------------------------------
    # Helpers -----------------------------------------------------------
    # ------------------------------------------------------------------
    @classmethod
    def _params_from_mapping(cls, mapping: Mapping[str, Any]) -> LightTransmissionCheckParams:
        data: Dict[str, Any] = {}
        for key in (
            "name",
            "calibration_enabled",
            "calibration_dark_gray",
            "calibration_bright_gray",
            "threshold_value",
        ):
            if key in mapping:
                data[key] = mapping[key]
        return cls._sanitize_params(LightTransmissionCheckParams(**data))

    @classmethod
    def _sanitize_params(cls, params: LightTransmissionCheckParams) -> LightTransmissionCheckParams:
        calibration_enabled = bool(params.calibration_enabled)
        dark = float(params.calibration_dark_gray)
        bright = float(params.calibration_bright_gray)
        if not np.isfinite(dark):
            dark = 0.0
        if not np.isfinite(bright):
            bright = 255.0
        dark = max(0.0, min(255.0, dark))
        bright = max(dark + 1e-6, min(255.0, bright))

        threshold = float(params.threshold_value)
        if not np.isfinite(threshold):
            threshold = 0.0
        threshold = max(0.0, threshold)
        if calibration_enabled:
            threshold = min(100.0, threshold)
        else:
            threshold = min(255.0, threshold)

        return LightTransmissionCheckParams(
            name=str(params.name or "Light Transmission Check"),
            calibration_enabled=calibration_enabled,
            calibration_dark_gray=dark,
            calibration_bright_gray=bright,
            threshold_value=threshold,
        )

    @staticmethod
    def _sanitize_thresholds(thresholds: Mapping[str, Any] | None) -> Dict[str, float | None]:
        if thresholds is None:
            return {
                "min_mean_gray": None,
                "max_mean_gray": None,
                "min_pct_above_T": None,
                "max_pct_above_T": None,
            }

        def _coerce(value: Any) -> float | None:
            if value is None:
                return None
            try:
                result = float(value)
            except (TypeError, ValueError):
                return None
            if not np.isfinite(result):
                return None
            return result

        return {
            "min_mean_gray": _coerce(thresholds.get("min_mean_gray")),
            "max_mean_gray": _coerce(thresholds.get("max_mean_gray")),
            "min_pct_above_T": _coerce(thresholds.get("min_pct_above_T")),
            "max_pct_above_T": _coerce(thresholds.get("max_pct_above_T")),
        }

    @staticmethod
    def _sanitize_mask(mask: np.ndarray | None, shape: Sequence[int]) -> np.ndarray:
        if mask is None:
            return np.ones(shape, dtype=bool)

        arr = np.asarray(mask)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        arr = arr.astype(np.uint8, copy=False)
        arr = np.where(arr > 0, 1, 0).astype(np.uint8)

        h, w = shape
        if arr.shape != (h, w):
            arr = arr[:h, :w]
            if arr.shape != (h, w):
                sanitized = np.zeros((h, w), dtype=np.uint8)
                sh, sw = arr.shape
                sanitized[: min(h, sh), : min(w, sw)] = arr[: min(h, sh), : min(w, sw)]
                arr = sanitized
        return arr.astype(bool)

    @staticmethod
    def _mask_from_prepared(prepared) -> np.ndarray:
        roi_h, roi_w = prepared.frame_roi.shape[:2]
        if prepared.valid_mask is None:
            return np.ones((roi_h, roi_w), dtype=bool)

        mask = np.zeros((roi_h, roi_w), dtype=bool)
        valid = np.asarray(prepared.valid_mask, dtype=bool)
        if valid.shape != (roi_h, roi_w):
            valid = valid[:roi_h, :roi_w]
        mask[valid] = True
        return mask

    def _compute_metrics(
        self,
        image_u8: np.ndarray,
        mask_bool: np.ndarray,
        params: LightTransmissionCheckParams,
    ) -> Dict[str, Any]:
        values = image_u8.astype(np.float32, copy=False)[mask_bool]
        total_pixels = int(values.size)

        if total_pixels == 0:
            return {
                "mean_gray": 0.0,
                "median_gray": 0.0,
                "min_gray": 0.0,
                "max_gray": 0.0,
                "std_gray": 0.0,
                "p10_gray": 0.0,
                "p90_gray": 0.0,
                "pct_above_T": 0.0,
                "pct_below_T": 0.0,
                "histogram_16bins": [0] * 16,
                "pixel_count": 0,
            }

        mean_gray = float(np.mean(values, dtype=np.float32))
        median_gray = float(np.median(values))
        min_gray = float(np.min(values))
        max_gray = float(np.max(values))
        std_gray = float(np.std(values, dtype=np.float32))
        p10_gray = float(np.percentile(values, 10))
        p90_gray = float(np.percentile(values, 90))

        histogram, _ = np.histogram(values, bins=16, range=(0.0, 255.0))
        histogram_list = [int(x) for x in histogram.tolist()]

        threshold = float(params.threshold_value)
        if params.calibration_enabled:
            denom = max(params.calibration_bright_gray - params.calibration_dark_gray, 1e-6)
            values_pct = np.clip(
                (values - params.calibration_dark_gray) / denom * 100.0,
                0.0,
                100.0,
            )
            threshold_pct = np.clip(threshold, 0.0, 100.0)
            above = float(np.count_nonzero(values_pct >= threshold_pct) * 100.0 / total_pixels)
            below = float(np.count_nonzero(values_pct < threshold_pct) * 100.0 / total_pixels)
            mean_transmission = float(np.mean(values_pct, dtype=np.float32))
            threshold_gray = float(
                params.calibration_dark_gray + (threshold_pct / 100.0) * denom
            )
        else:
            threshold_gray = np.clip(threshold, 0.0, 255.0)
            above = float(np.count_nonzero(values >= threshold_gray) * 100.0 / total_pixels)
            below = float(np.count_nonzero(values < threshold_gray) * 100.0 / total_pixels)
            mean_transmission = None

        metrics: Dict[str, Any] = {
            "mean_gray": mean_gray,
            "median_gray": median_gray,
            "min_gray": min_gray,
            "max_gray": max_gray,
            "std_gray": std_gray,
            "p10_gray": p10_gray,
            "p90_gray": p90_gray,
            "pct_above_T": above,
            "pct_below_T": below,
            "histogram_16bins": histogram_list,
            "pixel_count": total_pixels,
            "threshold_gray": float(threshold_gray),
        }

        if mean_transmission is not None:
            metrics["mean_transmission_pct"] = mean_transmission

        return metrics

    @staticmethod
    def _evaluate_thresholds(
        metrics: Dict[str, Any],
        params: LightTransmissionCheckParams,
        thresholds: Dict[str, float | None],
    ) -> tuple[bool, str | None]:
        if metrics.get("pixel_count", 0) == 0:
            return False, "empty_roi"

        mean_metric = (
            metrics.get("mean_transmission_pct")
            if params.calibration_enabled and metrics.get("mean_transmission_pct") is not None
            else metrics.get("mean_gray")
        )
        pct_above = metrics.get("pct_above_T")

        if thresholds.get("min_mean_gray") is not None and mean_metric is not None:
            if mean_metric < thresholds["min_mean_gray"]:
                return False, "mean_below_min"

        if thresholds.get("max_mean_gray") is not None and mean_metric is not None:
            if mean_metric > thresholds["max_mean_gray"]:
                return False, "mean_above_max"

        if thresholds.get("min_pct_above_T") is not None and pct_above is not None:
            if pct_above < thresholds["min_pct_above_T"]:
                return False, "pct_above_below_min"

        if thresholds.get("max_pct_above_T") is not None and pct_above is not None:
            if pct_above > thresholds["max_pct_above_T"]:
                return False, "pct_above_above_max"

        return True, None

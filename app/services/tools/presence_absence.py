"""Presence/absence tool measuring foreground area inside ROI with polarity support."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Sequence

import cv2
import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging
from app.utils.imaging import TimeBlockResult, time_block

ForegroundPolarity = Literal["bright", "dark"]
PresenceMode = Literal["present_when_area_in_range"]


@dataclass(slots=True)
class PresenceAbsenceCheckParams:
    """Configuration parameters for the presence / absence check tool."""

    name: str = "Prítomnosť / neprítomnosť"
    polarity: ForegroundPolarity = "bright"
    binary_threshold: int = 128
    gaussian_blur_kernel: int = 0
    presence_mode: PresenceMode = "present_when_area_in_range"
    min_area_px: int = 100
    max_area_px: int = 100_000
    min_fill_ratio: float = 0.0
    max_fill_ratio: float = 1.0


@dataclass(slots=True)
class ToolContext:
    """Runtime context passed to the lightweight ``run`` API."""

    params: PresenceAbsenceCheckParams = field(default_factory=PresenceAbsenceCheckParams)
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """Result returned by the lightweight ``run`` API."""

    ok: bool
    metrics: Dict[str, Any]
    debug_images: Dict[str, np.ndarray] = field(default_factory=dict)
    reason: str | None = None


class PresenceAbsenceCheckTool(PairTool):
    """Measure object presence by binarizing ROI and counting foreground area."""

    _ALLOWED_KERNELS = (0, 3, 5)

    def run(self, *args: Any, **kwargs: Any) -> ToolResult | ToolRunResult:  # type: ignore[override]
        """Dispatch between the lightweight API and the pipeline runner."""

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

        raise TypeError("Unsupported arguments for PresenceAbsenceCheckTool.run()")

    def _run_lightweight(
        self,
        image: np.ndarray,
        roi_mask: np.ndarray | None,
        context: ToolContext,
    ) -> ToolResult:
        params = self._sanitize_params(context.params)

        image_u8 = imaging.to_gray_u8(np.asarray(image))
        mask_u8 = self._sanitize_mask(roi_mask, image_u8.shape[:2])

        if params.gaussian_blur_kernel >= 3:
            image_u8 = cv2.GaussianBlur(
                image_u8,
                (params.gaussian_blur_kernel, params.gaussian_blur_kernel),
                0,
            )

        threshold_mode = cv2.THRESH_BINARY if params.polarity == "bright" else cv2.THRESH_BINARY_INV
        _, binary = cv2.threshold(
            image_u8,
            params.binary_threshold,
            255,
            threshold_mode,
        )

        if mask_u8 is not None:
            bin_roi = cv2.bitwise_and(binary, binary, mask=mask_u8)
            effective_pixels = int(np.count_nonzero(mask_u8))
        else:
            bin_roi = binary
            effective_pixels = int(binary.size)

        area_px = int(np.count_nonzero(bin_roi))
        fill_ratio = float(area_px / effective_pixels) if effective_pixels > 0 else 0.0

        reasons: list[str] = []
        if effective_pixels <= 0:
            reasons.append("effective_pixels is zero")
        if area_px < params.min_area_px:
            reasons.append("area_px below min")
        if area_px > params.max_area_px:
            reasons.append("area_px above max")
        if fill_ratio < params.min_fill_ratio:
            reasons.append("fill_ratio below min")
        if fill_ratio > params.max_fill_ratio:
            reasons.append("fill_ratio above max")

        ok = not reasons
        reason = "; ".join(reasons) if reasons else None

        metrics: Dict[str, Any] = {
            "area_px": area_px,
            "fill_ratio": fill_ratio,
            "threshold": int(params.binary_threshold),
            "effective_pixels": effective_pixels,
            "foreground_polarity": params.polarity,
        }
        if reason is not None:
            metrics["reason"] = reason

        return ToolResult(
            ok=ok,
            metrics=metrics,
            debug_images={"binary": bin_roi},
            reason=reason,
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
        timings: list[TimeBlockResult] = []

        params_dict = self._coerce_params_dict(params)
        thresholds_dict = self._coerce_thresholds_dict(thresholds)

        with time_block("prepare_pair", timings):
            self._ensure_pair_cache(frame, params_dict, thresholds_dict)
            prepared = self._prepare_pair(golden, frame, context)

        roi_frame = prepared.frame_roi
        roi_mask = self._mask_from_prepared(prepared)
        params_obj = self._params_from_mapping(params_dict, thresholds_dict)

        light_result = self._run_lightweight(roi_frame, roi_mask, ToolContext(params=params_obj))

        latency_ms = (time.perf_counter() - start) * 1000.0
        status = "ok" if light_result.ok else "nok"
        tool_id = self._prepared_context.get("tool_id", "presence_absence")

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
            "polarity": params_obj.polarity,
            "threshold": int(params_obj.binary_threshold),
            "presence_mode": params_obj.presence_mode,
            "area_px": int(light_result.metrics.get("area_px", 0)),
            "fill_ratio": float(light_result.metrics.get("fill_ratio", 0.0)),
            "effective_pixels": int(light_result.metrics.get("effective_pixels", 0)),
            "min_area_px": int(params_obj.min_area_px),
            "max_area_px": int(params_obj.max_area_px),
            "min_fill_ratio": float(params_obj.min_fill_ratio),
            "max_fill_ratio": float(params_obj.max_fill_ratio),
            "gaussian_blur_kernel": int(params_obj.gaussian_blur_kernel),
        }
        if light_result.reason is not None:
            diagnostics["reason"] = light_result.reason

        self.last_diagnostics = diagnostics

        metrics = {
            "area_px": int(light_result.metrics.get("area_px", 0)),
            "fill_ratio": float(light_result.metrics.get("fill_ratio", 0.0)),
            "threshold": int(params_obj.binary_threshold),
            "effective_pixels": int(light_result.metrics.get("effective_pixels", 0)),
            "foreground_polarity": params_obj.polarity,
        }
        if light_result.reason is not None:
            metrics["reason"] = light_result.reason

        result = self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=str(tool_id),
            debug_type="presence_absence",
            timings=timings,
        )

        artifacts = result.debug_artifacts
        if isinstance(artifacts, dict):
            diagnostics_payload = artifacts.setdefault("diagnostics", {})
            diagnostics_payload.setdefault("reason", light_result.reason)
            diagnostics_payload.setdefault("area_px", diagnostics["area_px"])
            diagnostics_payload.setdefault("fill_ratio", diagnostics["fill_ratio"])
            diagnostics_payload.setdefault("effective_pixels", diagnostics["effective_pixels"])
            diagnostics_payload.setdefault("threshold", diagnostics["threshold"])
            diagnostics_payload.setdefault("foreground_polarity", params_obj.polarity)
            diagnostics_payload.setdefault("min_area_px", diagnostics["min_area_px"])
            diagnostics_payload.setdefault("max_area_px", diagnostics["max_area_px"])
            diagnostics_payload.setdefault("min_fill_ratio", diagnostics["min_fill_ratio"])
            diagnostics_payload.setdefault("max_fill_ratio", diagnostics["max_fill_ratio"])

            preview = artifacts.setdefault("preview", {})
            preview.setdefault("frame", roi_frame)
            preview["binarization"] = light_result.debug_images.get("binary")

        return result

    @classmethod
    def _params_from_mapping(
        cls,
        params_mapping: Dict[str, Any],
        thresholds_mapping: Dict[str, Any],
    ) -> PresenceAbsenceCheckParams:
        data: Dict[str, Any] = {}
        for key in (
            "name",
            "polarity",
            "binary_threshold",
            "gaussian_blur_kernel",
            "presence_mode",
        ):
            if key in params_mapping:
                data[key] = params_mapping[key]
        for key in ("min_area_px", "max_area_px", "min_fill_ratio", "max_fill_ratio"):
            if key in thresholds_mapping:
                data[key] = thresholds_mapping[key]
        return cls._sanitize_params(PresenceAbsenceCheckParams(**data))

    @classmethod
    def _sanitize_params(cls, params: PresenceAbsenceCheckParams) -> PresenceAbsenceCheckParams:
        polarity: ForegroundPolarity = "dark" if str(params.polarity).lower() == "dark" else "bright"
        threshold = int(max(0, min(255, params.binary_threshold)))
        kernel = cls._normalize_kernel(params.gaussian_blur_kernel)
        presence_mode: PresenceMode = "present_when_area_in_range"

        min_area = int(max(0, params.min_area_px))
        max_area = int(max(min_area, params.max_area_px))
        min_fill = float(max(0.0, min(1.0, params.min_fill_ratio)))
        max_fill = float(max(min_fill, min(1.0, params.max_fill_ratio)))

        return PresenceAbsenceCheckParams(
            name=str(params.name or "Prítomnosť / neprítomnosť"),
            polarity=polarity,
            binary_threshold=threshold,
            gaussian_blur_kernel=kernel,
            presence_mode=presence_mode,
            min_area_px=min_area,
            max_area_px=max_area,
            min_fill_ratio=min_fill,
            max_fill_ratio=max_fill,
        )

    @classmethod
    def _normalize_kernel(cls, kernel: Any) -> int:
        try:
            value = int(kernel)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return 0
        closest = min(cls._ALLOWED_KERNELS, key=lambda item: abs(item - value))
        if closest == 0:
            return 3 if value >= 3 else 0
        return closest

    @staticmethod
    def _sanitize_mask(mask: np.ndarray | None, shape: Sequence[int]) -> np.ndarray | None:
        if mask is None:
            return np.ones(shape, dtype=np.uint8) * 255

        arr = np.asarray(mask)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        arr = arr.astype(np.uint8, copy=False)
        arr = np.where(arr > 0, 255, 0).astype(np.uint8)

        h, w = shape
        if arr.shape != (h, w):
            arr = arr[:h, :w]
            if arr.shape != (h, w):
                sanitized = np.zeros((h, w), dtype=np.uint8)
                sh, sw = arr.shape
                sanitized[: min(h, sh), : min(w, sw)] = arr[: min(h, sh), : min(w, sw)]
                arr = sanitized
        return arr

    @staticmethod
    def _mask_from_prepared(prepared) -> np.ndarray:
        roi_h, roi_w = prepared.frame_roi.shape[:2]
        if prepared.valid_mask is None:
            return np.ones((roi_h, roi_w), dtype=np.uint8) * 255

        mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
        valid = np.asarray(prepared.valid_mask, dtype=bool)
        if valid.shape != (roi_h, roi_w):
            valid = valid[:roi_h, :roi_w]
        mask[valid] = 255
        return mask

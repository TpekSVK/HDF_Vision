"""Measurement of light transmission using dark/open calibration frames."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Protocol, Tuple

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging
from app.utils.imaging import TimeBlockResult, time_block


class StorageService(Protocol):
    """Protocol describing minimal storage API needed for calibration IO."""

    def load_image(self, path: str) -> np.ndarray | None:  # pragma: no cover - protocol
        ...


@dataclass(slots=True)
class LightTransmissionCheckParams:
    """Configuration values for the light transmission measurement tool."""

    name: str = "Light Transmission Check"
    mode: Literal["transmission"] = "transmission"
    target_T_min: float = 0.35
    target_T_max: float = 0.55
    uniformity_max: float = 0.05
    percentile_bounds: Tuple[int, int] = (10, 90)
    gaussian_blur_kernel: int = 0
    flat_field: bool = False
    dark_path: Optional[str] = None
    open_path: Optional[str] = None


@dataclass(slots=True)
class ToolContext:
    """Runtime context passed to the lightweight run API."""

    params: LightTransmissionCheckParams = field(default_factory=LightTransmissionCheckParams)
    storage: Optional[StorageService] = None
    recipe_id: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """Result returned by the lightweight run API."""

    ok: bool
    metrics: Dict[str, Any]
    debug_images: Dict[str, np.ndarray] = field(default_factory=dict)
    reason: Optional[str] = None


class LightTransmissionCheckTool(PairTool):
    """Compute normalized light transmission using calibration images."""

    _ALLOWED_KERNELS = (0, 3, 5)

    def __init__(self) -> None:
        super().__init__()
        self._latest_params = LightTransmissionCheckParams()

    # ------------------------------------------------------------------
    # Calibration helpers
    # ------------------------------------------------------------------
    def load_calibration(
        self,
        storage: StorageService | None,
        recipe_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Load dark and open calibration images for the current recipe."""

        params = self._latest_params

        def _resolve_path(custom: Optional[str], default_name: str) -> Path:
            if custom:
                return Path(custom)
            base = Path("recipes") / recipe_id / "calib" / "light"
            return base / default_name

        dark_path = _resolve_path(params.dark_path, "dark.png")
        open_path = _resolve_path(params.open_path, "open.png")

        dark = self._read_calibration_frame(storage, dark_path)
        open_img = self._read_calibration_frame(storage, open_path)

        if dark is None or open_img is None:
            raise FileNotFoundError("missing calibration")

        dark_u8 = imaging.to_gray_u8(np.asarray(dark))
        open_u8 = imaging.to_gray_u8(np.asarray(open_img))

        return dark_u8, open_u8

    # ------------------------------------------------------------------
    # Lightweight execution
    # ------------------------------------------------------------------
    def run(self, *args: Any, **kwargs: Any) -> ToolResult | ToolRunResult:  # type: ignore[override]
        """Dispatch between lightweight helper API and pipeline execution."""

        if len(args) == 3 and not kwargs:
            image, roi_mask, context = args
            if not isinstance(context, ToolContext):
                raise TypeError("Expected ToolContext for lightweight execution")
            return self._run_lightweight(
                np.asarray(image),
                np.asarray(roi_mask) if roi_mask is not None else None,
                context,
            )

        if len(args) >= 4:
            golden, frame, params, thresholds = args[:4]
            context_payload = args[4] if len(args) > 4 else kwargs.get("context", {})
            return self._run_pipeline(
                np.asarray(golden),
                np.asarray(frame),
                params if isinstance(params, ToolParams) else ToolParams.from_obj(params),
                thresholds
                if isinstance(thresholds, ToolThresholds)
                else ToolThresholds.from_obj(thresholds),
                context_payload if isinstance(context_payload, dict) else {},
            )

        raise TypeError("Unsupported arguments for LightTransmissionCheckTool.run()")

    # ------------------------------------------------------------------
    # Lightweight execution
    # ------------------------------------------------------------------
    def _run_lightweight(
        self,
        image: np.ndarray,
        roi_mask: np.ndarray | None,
        context: ToolContext,
    ) -> ToolResult:
        params = self._sanitize_params(context.params)
        self._latest_params = params

        recipe_id = context.recipe_id or context.extras.get("recipe_id") if context.extras else None
        storage = context.storage or context.extras.get("storage") if context.extras else None

        image_u8 = imaging.to_gray_u8(np.asarray(image))
        mask_u8 = self._sanitize_mask(roi_mask, image_u8.shape)

        try:
            if not recipe_id and not (params.dark_path and params.open_path):
                raise FileNotFoundError("missing calibration")
            dark_u8, open_u8 = self.load_calibration(storage, recipe_id or "")
        except FileNotFoundError:
            return ToolResult(
                ok=False,
                metrics={},
                debug_images={},
                reason="missing calibration",
            )

        if dark_u8.shape != image_u8.shape or open_u8.shape != image_u8.shape:
            return ToolResult(
                ok=False,
                metrics={},
                debug_images={},
                reason="calibration size mismatch",
            )

        image_proc = image_u8
        if params.gaussian_blur_kernel >= 3:
            try:
                import cv2
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("Gaussian blur requested but OpenCV is not available") from exc

            image_proc = cv2.GaussianBlur(
                image_proc,
                (params.gaussian_blur_kernel, params.gaussian_blur_kernel),
                0,
            )

        I = image_proc.astype(np.float32)
        Id = dark_u8.astype(np.float32)
        Io = open_u8.astype(np.float32)

        denom = Io - Id
        denom[denom < 1] = 1
        if params.flat_field:
            ff = denom.astype(np.float32, copy=True)
            ff[ff < 1] = 1
            denom = ff

        T_full = (I - Id) / denom
        T_full = np.clip(T_full, 0.0, 1.0)

        mask_bool = mask_u8 > 0 if mask_u8 is not None else np.ones_like(T_full, dtype=bool)
        T_values = T_full[mask_bool]

        if T_values.size == 0:
            return ToolResult(
                ok=False,
                metrics={},
                debug_images={},
                reason="empty roi",
            )

        T_mean = float(np.mean(T_values))
        T_std = float(np.std(T_values))

        lo_perc, hi_perc = params.percentile_bounds
        p_lo = float(np.percentile(T_values, lo_perc))
        p_hi = float(np.percentile(T_values, hi_perc))

        in_range = params.target_T_min <= T_mean <= params.target_T_max
        uniform_ok = T_std <= params.uniformity_max
        perc_ok = params.target_T_min <= p_lo and p_hi <= params.target_T_max

        ok = bool(in_range and uniform_ok and perc_ok)

        reason: Optional[str] = None
        if not ok:
            if not in_range:
                reason = "mean transmission out of range"
            elif not uniform_ok:
                reason = "non-uniform transmission"
            elif not perc_ok:
                reason = "percentiles out of range"

        heat = (T_full * 255.0).astype(np.uint8)
        if mask_u8 is not None:
            mask_zero = mask_u8 == 0
            heat = heat.copy()
            heat[mask_zero] = 0

        metrics = {
            "T_mean": T_mean,
            "T_std": T_std,
            "T_p_lo": p_lo,
            "T_p_hi": p_hi,
        }

        debug_images = {"T_heat": heat, "input": image_u8.copy()}

        return ToolResult(
            ok=ok,
            metrics=metrics,
            debug_images=debug_images,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Pipeline integration
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

        recipe_id = context.get("recipe_id") or self._prepared_context.get("recipe_id")
        storage = self._prepared_context.get("storage")

        lightweight_context = ToolContext(
            params=params_obj,
            storage=storage,
            recipe_id=recipe_id,
            extras={"recipe_id": recipe_id, "storage": storage},
        )

        result_light = self._run_lightweight(roi_frame, roi_mask, lightweight_context)

        latency_ms = (time.perf_counter() - start) * 1000.0
        status = "ok" if result_light.ok else "nok"

        diagnostics = {
            "roi": {
                "x": int(prepared.roi_rect[0]),
                "y": int(prepared.roi_rect[1]),
                "w": int(prepared.roi_rect[2]),
                "h": int(prepared.roi_rect[3]),
            },
            "dx_total": float(prepared.dx_total),
            "dy_total": float(prepared.dy_total),
            "virtual_alignment": bool(prepared.virtual_alignment),
            "T_mean": float(result_light.metrics.get("T_mean", 0.0)),
            "T_std": float(result_light.metrics.get("T_std", 0.0)),
            "T_p_lo": float(result_light.metrics.get("T_p_lo", 0.0)),
            "T_p_hi": float(result_light.metrics.get("T_p_hi", 0.0)),
        }
        if result_light.reason:
            diagnostics["reason"] = result_light.reason

        self.last_diagnostics = diagnostics

        debug_artifacts: Dict[str, Any] = {
            "preview": {
                "frame": roi_frame,
                "mask": roi_mask,
                "heatmap": result_light.debug_images.get("T_heat"),
            },
            "diagnostics": diagnostics.copy(),
            "timings": [
                {"name": block.name, "elapsed_ms": float(block.elapsed_ms)}
                for block in timings
            ],
        }

        metrics = dict(result_light.metrics)
        metrics["latency_ms"] = float(latency_ms)

        return ToolRunResult(
            status=status,
            metrics=metrics,
            latency_ms=float(latency_ms),
            debug_artifacts=debug_artifacts,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _sanitize_params(self, params: LightTransmissionCheckParams | Any) -> LightTransmissionCheckParams:
        return self._params_from_mapping(params)

    @classmethod
    def _params_from_mapping(
        cls, params: LightTransmissionCheckParams | Dict[str, Any] | Any
    ) -> LightTransmissionCheckParams:
        obj = LightTransmissionCheckParams()
        if isinstance(params, LightTransmissionCheckParams):
            values: Dict[str, Any] = asdict(params)
        elif is_dataclass(params):
            values = asdict(params)
        elif isinstance(params, dict):
            values = dict(params)
        else:
            values = dict(getattr(params, "__dict__", {}) or {})

        for field_name in (
            "target_T_min",
            "target_T_max",
            "uniformity_max",
            "gaussian_blur_kernel",
            "flat_field",
            "percentile_bounds",
            "dark_path",
            "open_path",
        ):
            if field_name in values:
                setattr(obj, field_name, values[field_name])

        lo_key = values.get("percentile_lo")
        hi_key = values.get("percentile_hi")
        if lo_key is not None or hi_key is not None:
            lo_val = 10 if lo_key is None else int(lo_key)
            hi_val = 90 if hi_key is None else int(hi_key)
            obj.percentile_bounds = (lo_val, hi_val)

        kernel = int(getattr(obj, "gaussian_blur_kernel", 0) or 0)
        if kernel not in cls._ALLOWED_KERNELS:
            kernel = 0
        obj.gaussian_blur_kernel = kernel

        bounds = tuple(getattr(obj, "percentile_bounds", (10, 90)) or (10, 90))
        if len(bounds) != 2:
            bounds = (10, 90)
        lo, hi = int(bounds[0]), int(bounds[1])
        lo = max(0, min(100, lo))
        hi = max(0, min(100, hi))
        if lo > hi:
            lo, hi = hi, lo
        obj.percentile_bounds = (lo, hi)

        obj.target_T_min = float(obj.target_T_min)
        obj.target_T_max = float(obj.target_T_max)
        obj.uniformity_max = float(obj.uniformity_max)
        obj.flat_field = bool(obj.flat_field)

        return obj

    def _sanitize_mask(
        self,
        mask: np.ndarray | None,
        shape: tuple[int, int],
    ) -> np.ndarray | None:
        if mask is None:
            return None
        mask_arr = np.asarray(mask)
        if mask_arr.ndim == 3 and mask_arr.shape[2] > 0:
            mask_arr = mask_arr[:, :, 0]
        if mask_arr.dtype != np.uint8:
            mask_arr = np.clip(mask_arr, 0, 255).astype(np.uint8)
        if mask_arr.shape[:2] != shape[:2]:
            resized = np.zeros(shape, dtype=np.uint8)
            h = min(shape[0], mask_arr.shape[0])
            w = min(shape[1], mask_arr.shape[1])
            resized[:h, :w] = mask_arr[:h, :w]
            return resized
        return mask_arr

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

    def _read_calibration_frame(
        self,
        storage: StorageService | None,
        path: Path,
    ) -> np.ndarray | None:
        str_path = str(path)
        if storage is not None:
            loader = getattr(storage, "load_image", None)
            if callable(loader):
                try:
                    result = loader(str_path)
                    if result is not None:
                        return np.asarray(result)
                except FileNotFoundError:
                    return None
                except Exception:
                    pass

        full_path = Path("/data") / path
        if full_path.exists():
            try:
                import imageio.v3 as iio

                return np.asarray(iio.imread(full_path))
            except Exception:
                pass

        if path.exists():
            try:
                import imageio.v3 as iio

                return np.asarray(iio.imread(path))
            except Exception:
                pass

        return None


__all__ = [
    "LightTransmissionCheckTool",
    "LightTransmissionCheckParams",
    "ToolContext",
    "ToolResult",
]


"""Measurement of light transmission using dark/open calibration frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Protocol, Tuple

import numpy as np

from app.utils import imaging


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


class LightTransmissionCheckTool:
    """Compute normalized light transmission using calibration images."""

    _ALLOWED_KERNELS = (0, 3, 5)

    def __init__(self) -> None:
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
    def run(
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
    # Internal helpers
    # ------------------------------------------------------------------
    def _sanitize_params(self, params: LightTransmissionCheckParams | Any) -> LightTransmissionCheckParams:
        obj = LightTransmissionCheckParams()
        if isinstance(params, LightTransmissionCheckParams):
            values: Dict[str, Any] = dict(vars(params))
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
        if kernel not in self._ALLOWED_KERNELS:
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


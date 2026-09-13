"""Image geometry and cache keys shared by tool implementations."""
from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
import math
import numpy as np
from app.models.schema import ToolMask, ToolRoi


def _rect_from_any(value: Any) -> Optional[Tuple[int, int, int, int]]:
    """Normalize various ROI representations to an (x, y, w, h) tuple."""

    if value is None:
        return None
    if isinstance(value, ToolRoi):
        return value.rect()
    if isinstance(value, dict):
        keys = ("x", "y", "w", "h")
        if all(k in value for k in keys):
            try:
                return (
                    int(round(float(value["x"]))),
                    int(round(float(value["y"]))),
                    int(round(float(value["w"]))),
                    int(round(float(value["h"]))),
                )
            except Exception:
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            x, y, w, h = value[:4]
            return (
                int(round(float(x))),
                int(round(float(y))),
                int(round(float(w))),
                int(round(float(h))),
            )
        except Exception:
            return None
    return None


def _clamp_rect(
    rect: Optional[Tuple[int, int, int, int]], width: int, height: int
) -> Optional[Tuple[int, int, int, int]]:
    """Clamp a rectangle to image bounds. Returns ``None`` if empty."""

    if rect is None:
        return (0, 0, width, height)

    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None

    x1 = max(0, min(width, x))
    y1 = max(0, min(height, y))
    x2 = max(0, min(width, x + w))
    y2 = max(0, min(height, y + h))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2 - x1, y2 - y1


def _ensure_gray_u8(image: np.ndarray) -> np.ndarray:
    """Convert an image to a 2D ``uint8`` array without copying when possible."""

    import numpy as np

    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    return arr


def _freeze_value(value: Any) -> Any:
    if isinstance(value, ToolRoi):
        return _freeze_value(value.to_dict())
    if isinstance(value, ToolMask):
        mask = value.value
        if mask is None:
            return None
        return (
            mask.shape,
            mask.dtype.str,
            int(mask.__array_interface__["data"][0]),
        )
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, np.ndarray):
        return (
            value.shape,
            value.dtype.str,
            int(value.__array_interface__["data"][0]),
        )
    return value


def _freeze_dict(mapping: Dict[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if not mapping:
        return tuple()
    return tuple(sorted((str(k), _freeze_value(v)) for k, v in mapping.items()))


def _extract_translation_from_affine(T: np.ndarray | None) -> tuple[float, float]:
    """Return translation components from a 2×3 affine transform."""

    import numpy as np

    if T is None:
        return 0.0, 0.0

    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):  # pragma: no cover - defensive fallback
        return 0.0, 0.0

    return float(arr[0, 2]), float(arr[1, 2])


def _extract_rotation_from_affine(T: np.ndarray | None) -> float:
    """Return rotation angle in degrees encoded in a 2×3 affine transform."""

    if T is None:
        return 0.0

    arr = np.asarray(T, dtype=np.float32)
    if arr.shape != (2, 3):  # pragma: no cover - defensive fallback
        return 0.0

    angle = math.degrees(math.atan2(float(arr[1, 0]), float(arr[0, 0])))
    return float(angle)

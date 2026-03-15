"""Shared helpers for multi-view UI components."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from app.models.schema import RecipeView

_LOGGER = logging.getLogger(__name__)


def view_uses_global_golden(view: Optional[RecipeView]) -> bool:
    """Return ``True`` if the view should fall back to the legacy golden frame."""

    if view is None:
        return True

    golden_path = str(getattr(view, "golden_path", "") or "").strip()
    if not golden_path:
        return True

    return Path(golden_path).name == "golden.png"


def view_image_rotation(view: Optional[RecipeView]) -> int:
    """Return normalized per-view image rotation in degrees."""

    try:
        rotation = int(getattr(view, "image_rotation", 0) if view is not None else 0)
    except Exception:
        rotation = 0
    if rotation not in {0, 90, 180, 270}:
        rotation = 0
    return rotation


def apply_view_rotation(frame: np.ndarray | None, image_rotation: int, *, context: str = "") -> np.ndarray | None:
    """Apply explicit OpenCV rotation to a frame."""

    if frame is None:
        return None

    rotation = int(image_rotation) if image_rotation in {0, 90, 180, 270} else 0
    _LOGGER.info("[VIEW_ROTATION] view=%s rotation=%s", context or "n/a", rotation)

    if rotation == 0:
        return frame
    try:
        import cv2

        if rotation == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if rotation == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        if rotation == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    except Exception:
        if rotation == 90:
            return np.rot90(frame, k=3).copy()
        if rotation == 180:
            return np.rot90(frame, k=2).copy()
        if rotation == 270:
            return np.rot90(frame, k=1).copy()
    return frame


def apply_view_image_transform(frame: np.ndarray | None, view: Optional[RecipeView], *, stage: str = "") -> np.ndarray | None:
    """Apply per-view image transforms early in the pipeline."""

    rotation = view_image_rotation(view)
    view_id = getattr(view, "id", None) if view is not None else None
    transformed = apply_view_rotation(frame, rotation, context=str(view_id or "n/a"))
    if stage:
        _LOGGER.info("[VIEW_ROTATION] applied before %s", stage)
    return transformed

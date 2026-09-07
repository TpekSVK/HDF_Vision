"""Raster helpers for applying non-rectangular tool ROI geometry."""

from __future__ import annotations

from typing import Any, Optional, Tuple

import cv2
import numpy as np

from app.models.schema import ToolRoi


def roi_shape_mask(
    roi: ToolRoi | dict[str, Any] | None,
    roi_rect: Tuple[int, int, int, int],
) -> Optional[np.ndarray]:
    """Return a ROI-local boolean inclusion mask, or ``None`` for rectangles."""

    if not isinstance(roi, (ToolRoi, dict)):
        return None
    descriptor = ToolRoi.from_obj(roi)
    shape = descriptor.shape()
    if shape == "rect" or descriptor.rect() is None:
        return None

    x, y, width, height = (int(value) for value in roi_rect)
    if width <= 0 or height <= 0:
        return np.zeros((max(0, height), max(0, width)), dtype=bool)

    if shape == "polygon":
        points = descriptor.points()
        if len(points) < 3:
            return np.zeros((height, width), dtype=bool)
        local_points = np.asarray(
            [[point_x - x, point_y - y] for point_x, point_y in points],
            dtype=np.int32,
        )
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [local_points], 1)
        return mask.astype(bool)

    original = descriptor.rect()
    if shape == "ellipse" and original is not None:
        ellipse_x, ellipse_y, ellipse_w, ellipse_h = original
        if ellipse_w <= 0 or ellipse_h <= 0:
            return np.zeros((height, width), dtype=bool)
        pixel_x = np.arange(x, x + width, dtype=np.float32) + 0.5
        pixel_y = np.arange(y, y + height, dtype=np.float32) + 0.5
        norm_x = (pixel_x - (ellipse_x + ellipse_w / 2.0)) / (ellipse_w / 2.0)
        norm_y = (pixel_y - (ellipse_y + ellipse_h / 2.0)) / (ellipse_h / 2.0)
        return (norm_y[:, None] ** 2 + norm_x[None, :] ** 2) <= 1.0

    return None


def roi_local_exclusion_mask(
    roi: ToolRoi | dict[str, Any] | None,
    ignore_mask: np.ndarray | None,
) -> Optional[np.ndarray]:
    """Combine pixels outside the ROI shape with an optional Ignore Mask."""

    if not isinstance(roi, (ToolRoi, dict)):
        return None
    descriptor = ToolRoi.from_obj(roi)
    rect = descriptor.rect()
    if rect is None:
        return None
    x, y, width, height = rect
    shape_mask = roi_shape_mask(descriptor, rect)
    excluded = np.logical_not(shape_mask) if shape_mask is not None else None

    if ignore_mask is not None:
        value = np.asarray(ignore_mask)
        if value.ndim == 3:
            value = value[:, :, 0]
        local_ignore = None
        if value.shape[:2] == (height, width):
            local_ignore = value > 0
        elif value.shape[0] >= y + height and value.shape[1] >= x + width:
            local_ignore = value[y:y + height, x:x + width] > 0
        if local_ignore is not None:
            excluded = (
                local_ignore
                if excluded is None else np.logical_or(excluded, local_ignore)
            )

    return None if excluded is None else excluded.astype(np.uint8) * 255


__all__ = ["roi_local_exclusion_mask", "roi_shape_mask"]

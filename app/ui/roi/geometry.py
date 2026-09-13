"""Canvas bounds, handles and display constants shared by the drawing components."""
from __future__ import annotations
from enum import Enum
from typing import Optional, Tuple
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor

_ROI_COLOR = QColor(0, 200, 0, 200)


_MASK_COLOR = QColor(217, 70, 239, 102)


_OVERLAY_COLOR = QColor(0, 0, 0, 120)


ROI_WARN_PIXELS = 900_000


MAX_ROI_PIXELS = 1_600_000


MASK_WARN_PIXELS = 900_000


MAX_MASK_PIXELS = 1_600_000


def _format_pixels(count: int) -> str:
    """Format pixel counts using thin spaces for readability."""

    return f"{int(count):,}".replace(",", "\u202f")


def _clamp_point_to_rect(point: QPointF, rect: QRectF) -> QPointF:
    if rect.isNull():
        return point
    x = min(max(point.x(), rect.left()), rect.right())
    y = min(max(point.y(), rect.top()), rect.bottom())
    return QPointF(x, y)


__all__ = [
    "ROIEditor",
    "LocatorROIEditor",
    "MaskEditor",
    "MaskEditorState",
    "ROI_WARN_PIXELS",
    "MAX_ROI_PIXELS",
    "MASK_WARN_PIXELS",
    "MAX_MASK_PIXELS",
]


class RoiHandle(Enum):
    TOP_LEFT = "top_left"
    TOP = "top"
    TOP_RIGHT = "top_right"
    LEFT = "left"
    RIGHT = "right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM = "bottom"
    BOTTOM_RIGHT = "bottom_right"


def clamp_integer_rect(
    rect: Tuple[float, float, float, float], *, bounds: QRectF, minimum_size: int, handle: Optional[RoiHandle] = None
) -> Tuple[int, int, int, int]:
    """Shared image bounds for drawing, move, resize, numeric edits and nudges.

    Resize keeps the opposite edge fixed and clamps at the UI-02 minimum.
    Other edits preserve valid small legacy ROI sizes (down to one pixel).
    """
    x, y, width, height = rect
    if handle is not None:
        right, bottom = x + width, y + height
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.LEFT, RoiHandle.BOTTOM_LEFT):
            minimum = min(minimum_size, right - bounds.left())
            x = min(max(x, bounds.left()), right - minimum)
        if handle in (RoiHandle.TOP_RIGHT, RoiHandle.RIGHT, RoiHandle.BOTTOM_RIGHT):
            minimum = min(minimum_size, bounds.right() - x)
            right = max(min(right, bounds.right()), x + minimum)
        if handle in (RoiHandle.TOP_LEFT, RoiHandle.TOP, RoiHandle.TOP_RIGHT):
            minimum = min(minimum_size, bottom - bounds.top())
            y = min(max(y, bounds.top()), bottom - minimum)
        if handle in (RoiHandle.BOTTOM_LEFT, RoiHandle.BOTTOM, RoiHandle.BOTTOM_RIGHT):
            minimum = min(minimum_size, bounds.bottom() - y)
            bottom = max(min(bottom, bounds.bottom()), y + minimum)
        width, height = right - x, bottom - y
    x, y, width, height = (int(round(value)) for value in (x, y, width, height))
    width = min(max(1, width), int(round(bounds.width())))
    height = min(max(1, height), int(round(bounds.height())))
    x = min(max(int(round(bounds.left())), x), int(round(bounds.right())) - width)
    y = min(max(int(round(bounds.top())), y), int(round(bounds.bottom())) - height)
    return x, y, width, height

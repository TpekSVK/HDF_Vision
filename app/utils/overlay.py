from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np


ColorBGR = Tuple[int, int, int]
_DEFAULT_PALETTE: Tuple[ColorBGR, ...] = (
    (255, 99, 71),
    (65, 105, 225),
    (50, 205, 50),
    (255, 215, 0),
    (138, 43, 226),
    (0, 206, 209),
    (255, 105, 180),
)


def default_palette() -> Tuple[ColorBGR, ...]:
    """Return the default color palette used for overlay rendering."""

    return _DEFAULT_PALETTE


def _clamp_alpha(value: Any, default: int = 255) -> int:
    try:
        alpha = int(round(float(value)))
    except (TypeError, ValueError):
        alpha = int(default)
    return max(0, min(255, alpha))


def _ensure_color(value: Any, fallback: ColorBGR) -> ColorBGR:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            b, g, r = (int(round(float(v))) for v in value)
            return (
                max(0, min(255, b)),
                max(0, min(255, g)),
                max(0, min(255, r)),
            )
        except (TypeError, ValueError):
            pass
    if isinstance(value, str) and value.startswith("#") and len(value) in {7, 9}:
        try:
            hex_value = value.lstrip("#")
            r = int(hex_value[0:2], 16)
            g = int(hex_value[2:4], 16)
            b = int(hex_value[4:6], 16)
            return (b, g, r)
        except ValueError:
            pass
    return fallback


def _normalize_rect(data: Any) -> Optional[Tuple[float, float, float, float]]:
    if data is None:
        return None
    if isinstance(data, Mapping):
        try:
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            w = float(data.get("w", data.get("width", 0.0)))
            h = float(data.get("h", data.get("height", 0.0)))
        except (TypeError, ValueError):
            return None
        return (x, y, w, h)
    if isinstance(data, (list, tuple)) and len(data) == 4:
        try:
            x, y, w, h = (float(value) for value in data)
        except (TypeError, ValueError):
            return None
        return (x, y, w, h)
    return None


def _normalize_points(data: Any) -> Optional[np.ndarray]:
    if data is None:
        return None
    if isinstance(data, np.ndarray):
        if data.ndim == 2 and data.shape[1] == 2:
            return data.astype(np.float32, copy=False)
        if data.ndim == 1 and data.size % 2 == 0:
            reshaped = data.reshape(-1, 2)
            return reshaped.astype(np.float32, copy=False)
        return None
    if isinstance(data, Sequence):
        points: list[tuple[float, float]] = []
        for entry in data:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                try:
                    px = float(entry[0])
                    py = float(entry[1])
                except (TypeError, ValueError):
                    return None
                points.append((px, py))
        if points:
            return np.asarray(points, dtype=np.float32)
    return None


def _normalize_mask(data: Any) -> Optional[np.ndarray]:
    if data is None:
        return None
    arr = np.asarray(data)
    if arr.ndim == 3 and arr.shape[2] >= 1:
        arr = arr[:, :, 0]
    if arr.ndim != 2:
        return None
    return arr.astype(bool, copy=False)


@dataclass(slots=True)
class OverlayItem:
    """Normalized overlay primitive ready for rendering."""

    kind: str
    color: ColorBGR
    thickness: int = 1
    alpha: int = 255
    fill_alpha: Optional[int] = None
    z_index: int = 0
    rect: Optional[Tuple[float, float, float, float]] = None
    points: Optional[np.ndarray] = None
    mask: Optional[np.ndarray] = None
    closed: bool = True
    label: Optional[str] = None

    @classmethod
    def from_rect(
        cls,
        rect: Tuple[float, float, float, float],
        *,
        color: ColorBGR,
        thickness: int = 2,
        alpha: int = 220,
        z_index: int = 20,
        label: Optional[str] = None,
    ) -> "OverlayItem":
        return cls(
            kind="rect",
            color=color,
            thickness=max(1, int(thickness)),
            alpha=_clamp_alpha(alpha),
            z_index=int(z_index),
            rect=rect,
            label=label,
        )

    @classmethod
    def from_mask(
        cls,
        mask: np.ndarray,
        *,
        color: ColorBGR,
        alpha: int = 80,
        z_index: int = 0,
        label: Optional[str] = None,
    ) -> Optional["OverlayItem"]:
        normalized = _normalize_mask(mask)
        if normalized is None:
            return None
        if not normalized.any():
            return None
        return cls(
            kind="mask",
            color=color,
            alpha=_clamp_alpha(alpha),
            z_index=int(z_index),
            mask=normalized,
            label=label,
        )

    @classmethod
    def polyline(
        cls,
        points: np.ndarray,
        *,
        color: ColorBGR,
        thickness: int = 2,
        alpha: int = 255,
        closed: bool = False,
        z_index: int = 30,
        label: Optional[str] = None,
        fill_alpha: Optional[int] = None,
    ) -> Optional["OverlayItem"]:
        normalized = _normalize_points(points)
        if normalized is None or len(normalized) == 0:
            return None
        return cls(
            kind="polygon" if closed else "polyline",
            color=color,
            thickness=max(1, int(thickness)),
            alpha=_clamp_alpha(alpha),
            fill_alpha=_clamp_alpha(fill_alpha, 0) if fill_alpha is not None else None,
            z_index=int(z_index),
            points=normalized,
            closed=bool(closed),
            label=label,
        )


OverlayLike = Union[OverlayItem, Mapping[str, Any]]


def _flatten_display_sources(values: Any) -> list[Any]:
    entries: list[Any] = []

    def _append(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for elem in value:
                _append(elem)
        else:
            entries.append(value)

    _append(values)
    return entries


def parse_display_items(
    display_items: Any,
    *,
    default_color: ColorBGR,
    default_label: Optional[str] = None,
) -> List[OverlayItem]:
    items: List[OverlayItem] = []
    for entry in _flatten_display_sources(display_items):
        if isinstance(entry, OverlayItem):
            items.append(entry)
            continue
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind", entry.get("type", ""))).lower().strip()
        label = entry.get("label") or default_label
        color = _ensure_color(entry.get("color"), default_color)
        alpha = entry.get("alpha", entry.get("opacity", None))
        z_index = entry.get("z_index", entry.get("layer", 0))
        if kind in {"rect", "rectangle", "roi"}:
            rect_data = entry.get("rect")
            if rect_data is None:
                rect_data = (
                    entry.get("x"),
                    entry.get("y"),
                    entry.get("w", entry.get("width")),
                    entry.get("h", entry.get("height")),
                )
            rect = _normalize_rect(rect_data)
            if rect is None:
                continue
            items.append(
                OverlayItem.from_rect(
                    rect,
                    color=color,
                    thickness=int(entry.get("thickness", 2) or 2),
                    alpha=_clamp_alpha(alpha, 220),
                    z_index=int(z_index or 20),
                    label=label,
                )
            )
        elif kind in {"mask", "ignore_mask"}:
            mask = entry.get("mask")
            if mask is None and "data" in entry and "shape" in entry:
                try:
                    array = np.asarray(entry.get("data")).reshape(tuple(entry.get("shape")))
                except Exception:
                    array = None
                mask = array
            overlay_mask = OverlayItem.from_mask(
                mask,
                color=color,
                alpha=_clamp_alpha(alpha, 80),
                z_index=int(z_index or 0),
                label=label,
            )
            if overlay_mask is not None:
                items.append(overlay_mask)
        elif kind in {"polyline", "polygon", "contour"}:
            points = entry.get("points") or entry.get("contour")
            closed = kind != "polyline" or bool(entry.get("closed", True))
            overlay_poly = OverlayItem.polyline(
                points,
                color=color,
                thickness=int(entry.get("thickness", 2) or 2),
                alpha=_clamp_alpha(alpha, 255),
                closed=closed,
                z_index=int(z_index or 30),
                label=label,
                fill_alpha=entry.get("fill_alpha"),
            )
            if overlay_poly is not None:
                items.append(overlay_poly)
    return items


def tool_overlay_items(
    tool: "Tool",
    *,
    color: ColorBGR,
    display_items: Any = None,
    label: Optional[str] = None,
) -> List[OverlayItem]:
    from app.models.schema import Tool  # Local import to avoid circular dependencies

    if not isinstance(tool, Tool):
        raise TypeError("tool_overlay_items expects a Tool instance")

    label_value = label or (tool.name or tool.type)
    items: List[OverlayItem] = []

    rect = tool.roi.rect()
    if rect is not None:
        normalized = _normalize_rect(rect)
        if normalized is not None:
            items.append(
                OverlayItem.from_rect(
                    normalized,
                    color=color,
                    thickness=2,
                    alpha=220,
                    z_index=20,
                    label=label_value,
                )
            )

    mask_value = getattr(tool.ignore_mask, "value", None)
    if mask_value is not None:
        mask_item = OverlayItem.from_mask(
            mask_value,
            color=color,
            alpha=80,
            z_index=0,
            label=label_value,
        )
        if mask_item is not None:
            items.append(mask_item)

    items.extend(
        parse_display_items(
            display_items,
            default_color=color,
            default_label=label_value,
        )
    )

    return items


def render_overlay(
    frame_shape: Tuple[int, int],
    items: Sequence[OverlayItem],
) -> Optional[np.ndarray]:
    import cv2  # Local import to avoid heavy dependency at module import time

    height, width = frame_shape
    if height <= 0 or width <= 0:
        return None

    overlay_rgb = np.zeros((height, width, 3), dtype=np.uint8)
    overlay_alpha = np.zeros((height, width), dtype=np.uint8)

    ordered = sorted(
        enumerate(items), key=lambda value: (value[1].z_index, value[0])
    )

    for _, item in ordered:
        if item.kind == "mask" and item.mask is not None:
            mask = np.asarray(item.mask, dtype=bool)
            if mask.shape != (height, width):
                continue
            alpha_value = _clamp_alpha(item.alpha, 80)
            if alpha_value <= 0:
                continue
            overlay_rgb[mask] = item.color
            overlay_alpha[mask] = np.maximum(overlay_alpha[mask], alpha_value)
            continue

        if item.kind in {"rect", "polyline", "polygon"}:
            tmp_rgb = np.zeros_like(overlay_rgb)
            tmp_alpha = np.zeros_like(overlay_alpha)
            line_type = cv2.LINE_AA
            alpha_value = _clamp_alpha(item.alpha, 220)

            if item.kind == "rect" and item.rect is not None:
                x, y, w, h = item.rect
                if w <= 0 or h <= 0:
                    continue
                p1 = (int(round(x)), int(round(y)))
                p2 = (int(round(x + w - 1)), int(round(y + h - 1)))
                thickness = max(1, int(item.thickness))
                cv2.rectangle(tmp_rgb, p1, p2, item.color, thickness, line_type)
                cv2.rectangle(tmp_alpha, p1, p2, alpha_value, thickness, line_type)
            elif item.points is not None and len(item.points) >= 2:
                pts = np.round(item.points).astype(np.int32).reshape(-1, 1, 2)
                thickness = max(1, int(item.thickness))
                if item.closed:
                    if item.fill_alpha:
                        fill_alpha = _clamp_alpha(item.fill_alpha, alpha_value)
                        cv2.fillPoly(tmp_rgb, [pts], item.color, lineType=line_type)
                        cv2.fillPoly(tmp_alpha, [pts], fill_alpha, lineType=line_type)
                    cv2.polylines(tmp_rgb, [pts], True, item.color, thickness, line_type)
                    cv2.polylines(tmp_alpha, [pts], True, alpha_value, thickness, line_type)
                else:
                    cv2.polylines(tmp_rgb, [pts], False, item.color, thickness, line_type)
                    cv2.polylines(tmp_alpha, [pts], False, alpha_value, thickness, line_type)
            else:
                continue

            mask = tmp_alpha > 0
            overlay_rgb[mask] = tmp_rgb[mask]
            overlay_alpha[mask] = np.maximum(overlay_alpha[mask], tmp_alpha[mask])

    if not overlay_alpha.any() and not overlay_rgb.any():
        return None

    return np.dstack([overlay_rgb, overlay_alpha])


def apply_overlay(
    base_image: np.ndarray,
    overlay: Optional[np.ndarray],
) -> np.ndarray:
    if overlay is None or overlay.size == 0:
        return np.asarray(base_image).astype(np.uint8, copy=False)

    import cv2  # Local import to avoid heavy dependency at module import time

    frame = np.asarray(base_image)
    if frame.ndim == 2:
        frame_bgr = cv2.cvtColor(frame.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    elif frame.ndim == 3:
        if frame.shape[2] == 1:
            frame_bgr = cv2.cvtColor(frame[:, :, 0].astype(np.uint8), cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 3:
            frame_bgr = frame.astype(np.uint8)
        else:
            frame_bgr = frame[:, :, :3].astype(np.uint8)
    else:
        frame_bgr = frame.reshape(frame.shape[0], frame.shape[1], -1)[:, :, :3].astype(np.uint8)

    if overlay.shape[:2] != frame_bgr.shape[:2]:
        raise ValueError("Overlay and frame dimensions must match for composition")

    overlay_rgb = overlay[:, :, :3].astype(np.float32)
    overlay_alpha = overlay[:, :, 3].astype(np.float32) / 255.0
    alpha_expanded = overlay_alpha[:, :, np.newaxis]

    if not np.any(overlay_alpha > 0.0):
        return frame_bgr

    frame_float = frame_bgr.astype(np.float32)
    composed = overlay_rgb * alpha_expanded + frame_float * (1.0 - alpha_expanded)
    composed = np.clip(composed, 0.0, 255.0)
    return composed.astype(np.uint8)


def extract_display_items_from_artifacts(artifacts: Any) -> list[Any]:
    if not isinstance(artifacts, Mapping):
        return []
    return _flatten_display_sources(artifacts.get("display_items"))


__all__ = [
    "OverlayItem",
    "apply_overlay",
    "default_palette",
    "extract_display_items_from_artifacts",
    "parse_display_items",
    "render_overlay",
    "tool_overlay_items",
]


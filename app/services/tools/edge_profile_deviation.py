from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.common import PairTool
from app.utils import imaging
from app.utils.imaging import TimeBlockResult, time_block


class EdgeProfileDeviationTool(PairTool):
    """Measure edge profile deviation between two anchor points."""

    def run(  # type: ignore[override]
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: Dict[str, Any],
    ) -> ToolRunResult:
        start = time.perf_counter()
        timings: List[TimeBlockResult] = []

        params_dict = self._coerce_params_dict(params)
        thresholds_dict = self._coerce_thresholds_dict(thresholds)

        with time_block("prepare_pair", timings):
            self._ensure_pair_cache(frame, params_dict, thresholds_dict)
            prepared = self._prepare_pair(golden, frame, context)

        point_a = _parse_point(params_dict.get("point_a"))
        point_b = _parse_point(params_dict.get("point_b"))
        points_in_roi = bool(params_dict.get("points_in_roi", False))
        orientation = str(params_dict.get("orientation", "auto")).lower()

        if point_a is None or point_b is None:
            latency_ms = (time.perf_counter() - start) * 1000.0
            diagnostics = {
                "error": "Missing point_a or point_b",
                "roi": _format_roi(prepared.roi_rect),
            }
            return self._finalize_result(
                status="warn",
                metrics={"coverage": 0.0},
                diagnostics=diagnostics,
                latency_ms=latency_ms,
                tool_id=self._prepared_context.get("tool_id", "edge_profile_deviation"),
                debug_type="edge_profile_deviation",
                timings=timings,
            )

        ax, ay = point_a
        bx, by = point_b
        if not points_in_roi:
            rx, ry, _, _ = prepared.roi_rect
            ax -= rx
            ay -= ry
            bx -= rx
            by -= ry

        sigma = max(0.0, float(params_dict.get("blur_sigma", 1.0)))
        scan_step = max(1, int(params_dict.get("scan_step", 2)))
        edge_polarity = str(params_dict.get("edge_polarity", "any")).lower()
        grad_threshold = float(params_dict.get("grad_threshold", 15.0))
        grad_threshold = max(0.0, grad_threshold)
        search_half_window = max(1, int(params_dict.get("search_half_window", 20)))
        outlier_trim_pct = float(params_dict.get("outlier_trim_pct", 0.1))
        outlier_trim_pct = min(max(outlier_trim_pct, 0.0), 0.9)
        min_coverage = float(params_dict.get("min_coverage", 0.6))
        use_subpixel = bool(params_dict.get("use_subpixel", False))

        frame_roi = prepared.frame_roi
        if sigma > 1e-6:
            with time_block("blur", timings):
                frame_roi = imaging.blur_gaussian_u8(frame_roi, sigma)

        dx = bx - ax
        dy = by - ay
        if orientation == "auto":
            orientation = "horizontal" if abs(dx) >= abs(dy) else "vertical"

        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"

        grad = _compute_gradient(frame_roi, orientation)

        line = _line_from_points(ax, ay, bx, by)
        h, w = frame_roi.shape[:2]

        scan_positions = _scan_positions(ax, ay, bx, by, orientation, scan_step, w, h)
        total_scan_lines = len(scan_positions)

        edge_points: list[tuple[float, float]] = []
        with time_block("scan", timings):
            for pos in scan_positions:
                point = _find_edge_point(
                    grad,
                    prepared.valid_mask,
                    line,
                    orientation,
                    pos,
                    search_half_window,
                    edge_polarity,
                    grad_threshold,
                    use_subpixel,
                )
                if point is not None:
                    edge_points.append(point)

        trimmed_points = edge_points
        distances = _compute_distances(trimmed_points, line)
        if trimmed_points and outlier_trim_pct > 1e-6:
            trimmed_points, distances = _trim_outliers(trimmed_points, distances, outlier_trim_pct)

        found_points = len(trimmed_points)
        coverage = float(found_points / total_scan_lines) if total_scan_lines > 0 else 0.0

        abs_distances = [abs(value) for value in distances]
        max_dev = float(max(abs_distances)) if abs_distances else 0.0
        p95_dev = float(np.percentile(abs_distances, 95)) if abs_distances else 0.0

        scale_info = _resolve_scale(context, self._prepared_context, orientation)
        unit = scale_info.unit
        scale = scale_info.scale
        max_dev_scaled = max_dev * scale
        p95_dev_scaled = p95_dev * scale

        threshold_max = float(thresholds_dict.get("max_deviation_max", 0.1))
        coverage_min = float(thresholds_dict.get("coverage_min", min_coverage))

        status = _resolve_status(max_dev_scaled, coverage, threshold_max, coverage_min, found_points)
        latency_ms = (time.perf_counter() - start) * 1000.0

        diagnostics = {
            "roi": _format_roi(prepared.roi_rect),
            "dx_total": prepared.dx_total,
            "dy_total": prepared.dy_total,
            "virtual_alignment": prepared.virtual_alignment,
            "orientation": orientation,
            "points_in_roi": points_in_roi,
            "point_a_roi": {"x": float(ax), "y": float(ay)},
            "point_b_roi": {"x": float(bx), "y": float(by)},
            "line_ab": {"a": float(line[0]), "b": float(line[1]), "c": float(line[2])},
            "edge_points": [
                {"x": float(x), "y": float(y)} for x, y in trimmed_points
            ],
            "max_deviation_point": _max_deviation_point(trimmed_points, distances),
            "scan_lines": total_scan_lines,
            "found_points": found_points,
            "coverage": coverage,
            "sigma": sigma,
            "grad_threshold": grad_threshold,
            "scan_step": scan_step,
            "search_half_window": search_half_window,
            "outlier_trim_pct": outlier_trim_pct,
            "edge_polarity": edge_polarity,
            "use_subpixel": use_subpixel,
            "calibration": {
                "unit": unit,
                "scale": scale,
                "mm_per_px": scale_info.mm_per_px,
                "scale_x_mm_per_px": scale_info.scale_x_mm_per_px,
                "scale_y_mm_per_px": scale_info.scale_y_mm_per_px,
            },
        }

        metrics = {
            "max_deviation": float(round(max_dev_scaled, 4)),
            "p95_deviation": float(round(p95_dev_scaled, 4)),
            "coverage": float(round(coverage, 4)),
            "unit": unit,
        }

        return self._finalize_result(
            status=status,
            metrics=metrics,
            diagnostics=diagnostics,
            latency_ms=latency_ms,
            tool_id=self._prepared_context.get("tool_id", "edge_profile_deviation"),
            debug_type="edge_profile_deviation",
            timings=timings,
        )


def _parse_point(value: Any) -> Optional[tuple[float, float]]:
    if value is None:
        return None
    if isinstance(value, dict):
        if "x" in value and "y" in value:
            return float(value["x"]), float(value["y"])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    return None


def _format_roi(rect: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, w, h = rect
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def _compute_gradient(frame: np.ndarray, orientation: str) -> np.ndarray:
    if orientation == "horizontal":
        return cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=3)


def _line_from_points(ax: float, ay: float, bx: float, by: float) -> tuple[float, float, float]:
    a = ay - by
    b = bx - ax
    c = ax * by - bx * ay
    return a, b, c


def _scan_positions(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    orientation: str,
    scan_step: int,
    width: int,
    height: int,
) -> list[int]:
    if orientation == "horizontal":
        start = int(round(min(ax, bx)))
        end = int(round(max(ax, bx)))
        start = max(0, min(width - 1, start))
        end = max(0, min(width - 1, end))
        if end < start:
            return []
        return list(range(start, end + 1, scan_step))

    start = int(round(min(ay, by)))
    end = int(round(max(ay, by)))
    start = max(0, min(height - 1, start))
    end = max(0, min(height - 1, end))
    if end < start:
        return []
    return list(range(start, end + 1, scan_step))


def _expected_position(
    line: tuple[float, float, float],
    orientation: str,
    scan_pos: int,
    fallback: float,
) -> float:
    a, b, c = line
    if orientation == "horizontal":
        if abs(b) < 1e-6:
            return fallback
        return -(a * scan_pos + c) / b
    if abs(a) < 1e-6:
        return fallback
    return -(b * scan_pos + c) / a


def _find_edge_point(
    grad: np.ndarray,
    valid_mask: Optional[np.ndarray],
    line: tuple[float, float, float],
    orientation: str,
    scan_pos: int,
    search_half_window: int,
    edge_polarity: str,
    grad_threshold: float,
    use_subpixel: bool,
) -> Optional[tuple[float, float]]:
    h, w = grad.shape[:2]
    if orientation == "horizontal":
        x = scan_pos
        if x < 0 or x >= w:
            return None
        expected = _expected_position(line, orientation, x, fallback=0.0)
        start = int(round(expected - search_half_window))
        end = int(round(expected + search_half_window))
        start = max(0, min(h - 1, start))
        end = max(0, min(h - 1, end))
        if end < start:
            return None
        values = grad[start : end + 1, x]
        mask = None
        if valid_mask is not None:
            mask = valid_mask[start : end + 1, x]
        pos = _pick_edge_position(values, mask, edge_polarity, grad_threshold, use_subpixel)
        if pos is None:
            return None
        return float(x), float(start + pos)

    y = scan_pos
    if y < 0 or y >= h:
        return None
    expected = _expected_position(line, orientation, y, fallback=0.0)
    start = int(round(expected - search_half_window))
    end = int(round(expected + search_half_window))
    start = max(0, min(w - 1, start))
    end = max(0, min(w - 1, end))
    if end < start:
        return None
    values = grad[y, start : end + 1]
    mask = None
    if valid_mask is not None:
        mask = valid_mask[y, start : end + 1]
    pos = _pick_edge_position(values, mask, edge_polarity, grad_threshold, use_subpixel)
    if pos is None:
        return None
    return float(start + pos), float(y)


def _pick_edge_position(
    values: np.ndarray,
    valid_mask: Optional[np.ndarray],
    edge_polarity: str,
    grad_threshold: float,
    use_subpixel: bool,
) -> Optional[float]:
    if values.size == 0:
        return None
    metric = values.astype(np.float32)
    if edge_polarity == "light_to_dark":
        metric = -metric
    elif edge_polarity == "any":
        metric = np.abs(metric)

    if valid_mask is not None:
        valid = valid_mask.astype(bool)
        if not np.any(valid):
            return None
        metric = metric.copy()
        metric[~valid] = -np.inf

    if not np.isfinite(metric).any():
        return None
    idx = int(np.argmax(metric))
    best_metric = metric[idx]
    if not np.isfinite(best_metric):
        return None

    strength = abs(values[idx])
    if strength < grad_threshold:
        return None

    if not use_subpixel or idx <= 0 or idx >= metric.size - 1:
        return float(idx)

    v1 = float(metric[idx - 1])
    v2 = float(metric[idx])
    v3 = float(metric[idx + 1])
    denom = v1 - 2.0 * v2 + v3
    if abs(denom) < 1e-6:
        return float(idx)
    delta = 0.5 * (v1 - v3) / denom
    delta = float(max(-0.5, min(0.5, delta)))
    return float(idx) + delta


def _compute_distances(
    points: Iterable[tuple[float, float]],
    line: tuple[float, float, float],
) -> list[float]:
    a, b, c = line
    denom = math.hypot(a, b)
    if denom < 1e-6:
        return [0.0 for _ in points]
    return [float((a * x + b * y + c) / denom) for x, y in points]


def _trim_outliers(
    points: list[tuple[float, float]],
    distances: list[float],
    trim_pct: float,
) -> tuple[list[tuple[float, float]], list[float]]:
    if not points:
        return points, distances
    trim_count = int(math.floor(len(points) * trim_pct))
    if trim_count <= 0:
        return points, distances
    order = np.argsort(np.abs(np.asarray(distances)))
    keep_count = max(1, len(points) - trim_count)
    keep_idx = set(order[:keep_count].tolist())
    trimmed_points = [pt for idx, pt in enumerate(points) if idx in keep_idx]
    trimmed_distances = [dist for idx, dist in enumerate(distances) if idx in keep_idx]
    return trimmed_points, trimmed_distances


def _max_deviation_point(
    points: list[tuple[float, float]],
    distances: list[float],
) -> Optional[dict[str, float]]:
    if not points or not distances:
        return None
    idx = int(np.argmax(np.abs(np.asarray(distances))))
    x, y = points[idx]
    return {"x": float(x), "y": float(y), "deviation": float(distances[idx])}


class _ScaleInfo:
    def __init__(
        self,
        unit: str,
        scale: float,
        mm_per_px: Optional[float],
        scale_x_mm_per_px: Optional[float],
        scale_y_mm_per_px: Optional[float],
    ) -> None:
        self.unit = unit
        self.scale = scale
        self.mm_per_px = mm_per_px
        self.scale_x_mm_per_px = scale_x_mm_per_px
        self.scale_y_mm_per_px = scale_y_mm_per_px


def _resolve_scale(
    context: Dict[str, Any],
    prepared_context: Dict[str, Any],
    orientation: str,
) -> _ScaleInfo:
    combined: dict[str, Any] = {}
    combined.update(prepared_context)
    combined.update(context)
    calibration = combined.get("calibration")
    mm_per_px = combined.get("mm_per_px")
    scale_x = combined.get("scale_x_mm_per_px")
    scale_y = combined.get("scale_y_mm_per_px")
    if isinstance(calibration, dict):
        mm_per_px = calibration.get("mm_per_px", mm_per_px)
        scale_x = calibration.get("scale_x_mm_per_px", scale_x)
        scale_y = calibration.get("scale_y_mm_per_px", scale_y)

    mm_per_px_val = _safe_float(mm_per_px)
    scale_x_val = _safe_float(scale_x)
    scale_y_val = _safe_float(scale_y)

    scale = 1.0
    unit = "px"
    if orientation == "horizontal":
        if scale_y_val is not None:
            scale = scale_y_val
            unit = "mm"
        elif mm_per_px_val is not None:
            scale = mm_per_px_val
            unit = "mm"
    else:
        if scale_x_val is not None:
            scale = scale_x_val
            unit = "mm"
        elif mm_per_px_val is not None:
            scale = mm_per_px_val
            unit = "mm"

    return _ScaleInfo(
        unit=unit,
        scale=float(scale),
        mm_per_px=mm_per_px_val,
        scale_x_mm_per_px=scale_x_val,
        scale_y_mm_per_px=scale_y_val,
    )


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_status(
    max_dev: float,
    coverage: float,
    max_threshold: float,
    min_coverage: float,
    found_points: int,
) -> str:
    if found_points <= 0:
        return "warn"
    if coverage < min_coverage or max_dev > max_threshold:
        return "nok"
    return "ok"

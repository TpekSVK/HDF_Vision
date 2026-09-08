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

        tangent, normal = _line_vectors(ax, ay, bx, by)
        grad = _compute_normal_gradient(frame_roi, normal)

        line = _line_from_points(ax, ay, bx, by)
        h, w = frame_roi.shape[:2]

        scan_positions = _scan_distances(ax, ay, bx, by, scan_step)
        total_scan_lines = len(scan_positions)

        edge_points: list[tuple[float, float]] = []
        with time_block("scan", timings):
            for pos in scan_positions:
                point = _find_edge_point_normal(
                    grad,
                    prepared.valid_mask,
                    (ax, ay),
                    tangent,
                    normal,
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

        scale_info = _resolve_scale(context, self._prepared_context, normal)
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
            "scan_geometry": "normal_to_ab",
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


def detect_guided_reference_edge(
    frame: np.ndarray,
    roi_rect: tuple[int, int, int, int],
    point_a: tuple[float, float],
    point_b: tuple[float, float],
    *,
    blur_sigma: float = 1.0,
    scan_step: int = 2,
    edge_polarity: str = "any",
    grad_threshold: float = 15.0,
    search_half_window: int = 20,
    outlier_trim_pct: float = 0.1,
    use_subpixel: bool = False,
    valid_mask: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Refine an approximate A-B line by scanning only its nearby band.

    This setup helper deliberately uses the same gradient and scan primitives as
    runtime inspection. Coordinates in the returned payload are image-global.
    """
    image = np.asarray(frame)
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim != 2:
        raise ValueError("Detekcia hrany očakáva sivý alebo BGR obraz.")

    image = np.clip(image, 0, 255).astype(np.uint8, copy=False)
    x, y, width, height = [int(value) for value in roi_rect]
    x0 = max(0, min(image.shape[1] - 1, x))
    y0 = max(0, min(image.shape[0] - 1, y))
    x1 = max(x0 + 1, min(image.shape[1], x + width))
    y1 = max(y0 + 1, min(image.shape[0], y + height))
    roi = image[y0:y1, x0:x1]
    if roi.shape[0] < 3 or roi.shape[1] < 3:
        raise ValueError("ROI je príliš malá pre navádzanú detekciu hrany.")
    if valid_mask is not None:
        valid_mask = np.asarray(valid_mask, dtype=bool)
        if valid_mask.shape != roi.shape:
            raise ValueError("Maska ROI nemá rovnaký rozmer ako zvolená oblasť.")

    ax, ay = float(point_a[0] - x0), float(point_a[1] - y0)
    bx, by = float(point_b[0] - x0), float(point_b[1] - y0)
    if math.hypot(bx - ax, by - ay) < 2.0:
        raise ValueError("Body A a B sú príliš blízko pri sebe.")

    tangent, normal = _line_vectors(ax, ay, bx, by)

    sigma = max(0.0, float(blur_sigma))
    prepared = imaging.blur_gaussian_u8(roi, sigma) if sigma > 1e-6 else roi
    grad = _compute_normal_gradient(prepared, normal)
    line = _line_from_points(ax, ay, bx, by)
    scan_positions = _scan_distances(ax, ay, bx, by, max(1, int(scan_step)))
    if not scan_positions:
        raise ValueError("Čiara A-B neprechádza zvolenou ROI.")

    requested_threshold = max(0.0, float(grad_threshold))
    threshold_candidates = [requested_threshold]
    if requested_threshold > 2.0:
        threshold_candidates.extend((requested_threshold * 0.5, max(2.0, requested_threshold * 0.25)))

    best_points: list[tuple[float, float]] = []
    used_threshold = requested_threshold
    for candidate_threshold in threshold_candidates:
        points = []
        for position in scan_positions:
            point = _find_edge_point_normal(
                grad,
                valid_mask,
                (ax, ay),
                tangent,
                normal,
                position,
                max(1, int(search_half_window)),
                str(edge_polarity or "any").lower(),
                candidate_threshold,
                bool(use_subpixel),
            )
            if point is not None:
                points.append(point)
        if len(points) > len(best_points):
            best_points = points
            used_threshold = candidate_threshold
        if len(points) / len(scan_positions) >= 0.6:
            break

    distances = _compute_distances(best_points, line)
    trimmed_points, _ = _trim_outliers(
        best_points,
        distances,
        min(max(float(outlier_trim_pct), 0.0), 0.9),
    )
    if len(trimmed_points) < 2:
        raise ValueError("V okolí čiary A-B sa nenašla súvislá hrana.")

    fit_input = np.asarray(trimmed_points, dtype=np.float32).reshape(-1, 1, 2)
    vx, vy, fit_x, fit_y = [
        float(value) for value in cv2.fitLine(fit_input, cv2.DIST_HUBER, 0, 0.01, 0.01).reshape(-1)
    ]

    def project(px: float, py: float) -> tuple[float, float]:
        distance = (px - fit_x) * vx + (py - fit_y) * vy
        return fit_x + distance * vx, fit_y + distance * vy

    refined_a = project(ax, ay)
    refined_b = project(bx, by)
    global_points = [(px + x0, py + y0) for px, py in trimmed_points]
    return {
        "point_a": (refined_a[0] + x0, refined_a[1] + y0),
        "point_b": (refined_b[0] + x0, refined_b[1] + y0),
        "edge_points": global_points,
        "coverage": float(len(trimmed_points) / len(scan_positions)),
        "found_points": len(trimmed_points),
        "scan_lines": len(scan_positions),
        "scan_geometry": "normal_to_ab",
        "grad_threshold": float(used_threshold),
        "recommended_params": {
            "grad_threshold": float(used_threshold),
        },
    }


def _format_roi(rect: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, w, h = rect
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def _line_vectors(
    ax: float, ay: float, bx: float, by: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-6:
        raise ValueError("Body A a B sú príliš blízko pri sebe.")
    tangent = ((bx - ax) / length, (by - ay) / length)
    return tangent, (-tangent[1], tangent[0])


def _compute_normal_gradient(
    frame: np.ndarray, normal: tuple[float, float]
) -> np.ndarray:
    grad_x = cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=3)
    return grad_x * float(normal[0]) + grad_y * float(normal[1])


def _line_from_points(ax: float, ay: float, bx: float, by: float) -> tuple[float, float, float]:
    a = ay - by
    b = bx - ax
    c = ax * by - bx * ay
    return a, b, c


def _scan_distances(
    ax: float, ay: float, bx: float, by: float, scan_step: int,
) -> list[int]:
    length = math.hypot(bx - ax, by - ay)
    if length < 1.0:
        return []
    return list(range(0, int(math.floor(length)) + 1, max(1, int(scan_step))))


def _find_edge_point_normal(
    grad: np.ndarray,
    valid_mask: Optional[np.ndarray],
    origin: tuple[float, float],
    tangent: tuple[float, float],
    normal: tuple[float, float],
    scan_distance: int,
    search_half_window: int,
    edge_polarity: str,
    grad_threshold: float,
    use_subpixel: bool,
) -> Optional[tuple[float, float]]:
    h, w = grad.shape[:2]
    base_x = origin[0] + tangent[0] * float(scan_distance)
    base_y = origin[1] + tangent[1] * float(scan_distance)
    offsets = np.arange(-int(search_half_window), int(search_half_window) + 1, dtype=np.float32)
    xs = base_x + offsets * normal[0]
    ys = base_y + offsets * normal[1]
    values, inside = _sample_bilinear(grad, xs, ys)
    if not np.any(inside):
        return None
    mask = np.zeros(offsets.shape, dtype=bool)
    mask[inside] = True
    if valid_mask is not None:
        sample_x = np.rint(xs[inside]).astype(np.int32)
        sample_y = np.rint(ys[inside]).astype(np.int32)
        mask[inside] = valid_mask[sample_y, sample_x]
    pos = _pick_edge_position(values, mask, edge_polarity, grad_threshold, use_subpixel)
    if pos is None:
        return None
    offset = float(-int(search_half_window) + pos)
    return base_x + offset * normal[0], base_y + offset * normal[1]


def _sample_bilinear(
    image: np.ndarray, xs: np.ndarray, ys: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample a 1D profile without staircase artefacts on diagonal edges."""
    height, width = image.shape[:2]
    inside = (xs >= 0.0) & (xs <= width - 1) & (ys >= 0.0) & (ys <= height - 1)
    values = np.full(xs.shape, -np.inf, dtype=np.float32)
    if not np.any(inside):
        return values, inside
    sample_x = xs[inside]
    sample_y = ys[inside]
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    dx = sample_x - x0
    dy = sample_y - y0
    values[inside] = (
        image[y0, x0] * (1.0 - dx) * (1.0 - dy)
        + image[y0, x1] * dx * (1.0 - dy)
        + image[y1, x0] * (1.0 - dx) * dy
        + image[y1, x1] * dx * dy
    )
    return values, inside


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
    if not all(math.isfinite(value) for value in (v1, v2, v3)):
        return float(idx)
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
    normal: tuple[float, float],
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
    if scale_x_val is not None and scale_y_val is not None:
        nx, ny = normal
        scale = math.hypot(float(nx) * scale_x_val, float(ny) * scale_y_val)
        unit = "mm"
    elif mm_per_px_val is not None:
        scale = mm_per_px_val
        unit = "mm"
    elif scale_x_val is not None:
        scale = scale_x_val
        unit = "mm"
    elif scale_y_val is not None:
        scale = scale_y_val
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

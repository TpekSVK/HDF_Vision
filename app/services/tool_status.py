"""Decision thresholds and normalized metric statuses."""
from __future__ import annotations
from typing import Any, Dict, Literal
from app.models.schema import ToolThresholds


DEFAULT_SSIM_MIN = 0.92


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_thresholds_dict(
    thresholds: Dict[str, Any] | ToolThresholds | None,
) -> Dict[str, Any]:
    if isinstance(thresholds, ToolThresholds):
        return dict(thresholds.values or {})
    return dict(thresholds or {})


def status_from_metrics(
    tool_type: str,
    metrics: Dict[str, Any] | None,
    thresholds: Dict[str, Any] | ToolThresholds | None,
) -> Literal["ok", "warn", "nok"]:
    """Map raw metric values to a normalized status for diagnostics."""

    tool_key = (tool_type or "").lower()
    metric_values = dict(metrics or {})
    threshold_values = _coerce_thresholds_dict(thresholds)

    if tool_key == "locator.template_match":
        corr = _safe_float(metric_values.get("corr"), 0.0)
        attempts = _safe_int(metric_values.get("match_attempts"), -1)
        corr_threshold = _safe_float(threshold_values.get("threshold_corr"), 0.55)
        if attempts == 0 and abs(corr) < 1e-6:
            return "warn"
        return "ok" if corr >= corr_threshold else "nok"

    if tool_key == "ssim":
        ssim_min = _safe_float(
            threshold_values.get("ssim_min"), DEFAULT_SSIM_MIN
        )
        ssim_val = _safe_float(metric_values.get("ssim"), 0.0)
        return "ok" if ssim_val >= ssim_min else "nok"


    if tool_key == "mse":
        mse_max = _safe_float(threshold_values.get("mse_max"), 25.0)
        mse_val = _safe_float(metric_values.get("mse"), float("inf"))
        return "ok" if mse_val <= mse_max else "nok"

    if tool_key == "ncc":
        ncc_min = _safe_float(threshold_values.get("ncc_min"), 0.9)
        ncc_val = _safe_float(metric_values.get("ncc"), -1.0)
        return "ok" if ncc_val >= ncc_min else "nok"

    if tool_key == "edge_change":
        edge_ratio_max = _safe_float(threshold_values.get("edge_ratio_max"), 0.05)
        edge_ratio = _safe_float(metric_values.get("edge_ratio"), 0.0)
        effective_pixels = metric_values.get("effective_pixels")
        if effective_pixels is not None and _safe_int(effective_pixels, 0) <= 0:
            return "warn"
        return "ok" if edge_ratio <= edge_ratio_max else "nok"

    if tool_key == "edge_profile_deviation":
        max_dev_max = _safe_float(threshold_values.get("max_deviation_max"), 0.1)
        coverage_min = _safe_float(threshold_values.get("coverage_min"), 0.6)
        max_dev = _safe_float(metric_values.get("max_deviation"), 0.0)
        coverage = _safe_float(metric_values.get("coverage"), 0.0)
        if coverage <= 0.0:
            return "warn"
        if max_dev > max_dev_max or coverage < coverage_min:
            return "nok"
        return "ok"

    return "ok" if metric_values else "warn"

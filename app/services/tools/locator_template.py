"""Template locator implementation."""
from __future__ import annotations
from typing import Any, Dict, Optional, Sequence, Tuple
import math
import time
import numpy as np
from app.services.roi_geometry import roi_shape_mask
from app.models.schema import Tool, ToolParams, ToolRoi, ToolThresholds
from app.services.tool_contracts import BaseTool, ToolRunResult
from app.services.tool_image_helpers import _clamp_rect, _ensure_gray_u8, _freeze_value, _rect_from_any
from app.services.tool_status import _safe_float, _safe_int, status_from_metrics


class LocatorTemplateMatchTool(BaseTool):
    """ITool implementation wrapping template matching locator."""

    def __init__(self) -> None:
        super().__init__()
        self._cache_signature: Any | None = None
        self._match_cache: dict[str, Any] = {}

    def prepare(self, context: dict[str, Any]) -> None:  # type: ignore[override]
        super().prepare(context)
        self._cache_signature = None
        self._match_cache.clear()

    def teardown(self) -> None:  # type: ignore[override]
        super().teardown()
        self._cache_signature = None
        self._match_cache.clear()

    def run(  # type: ignore[override]
        self,
        golden: "np.ndarray",
        frame: "np.ndarray",
        params: ToolParams,
        thresholds: ToolThresholds,
        context: dict[str, Any],
    ) -> ToolRunResult:
        tool: Optional[Tool] = self._prepared_context.get("tool")
        roi = tool.roi if isinstance(tool, Tool) else context.get("roi", ToolRoi())
        tool_id = self._prepared_context.get("tool_id") or (tool.name if isinstance(tool, Tool) else "locator")

        params_obj = params if isinstance(params, ToolParams) else ToolParams.from_obj(params)
        thresholds_obj = (
            thresholds if isinstance(thresholds, ToolThresholds) else ToolThresholds.from_obj(thresholds)
        )

        params_dict = dict(params_obj.values or {})
        if "template_roi" not in params_dict and isinstance(tool, Tool):
            template_roi = getattr(tool, "template_roi", ToolRoi()).to_dict()
            if template_roi:
                params_dict["template_roi"] = template_roi
                params_obj = ToolParams(params_dict)
        thresholds_dict = thresholds_obj.values or {}

        template_signature = (
            tuple(np.asarray(frame).shape[:2]),
            (
                bool(params_dict.get("use_golden_crop", True)),
                _freeze_value(params_dict.get("template_roi")),
                _safe_int(params_dict.get("coarse_cap", 600), 600),
                str(params_dict.get("alignment_mode", "translation")),
                round(_safe_float(params_dict.get("angle_range_deg", 15.0), 15.0), 4),
                round(_safe_float(params_dict.get("angle_step_deg", 1.0), 1.0), 4),
                _freeze_value(params_dict.get("reference_point_a")),
                _freeze_value(params_dict.get("reference_point_b")),
                _safe_int(params_dict.get("reference_search_half_window", 20), 20),
                _safe_int(params_dict.get("reference_scan_step", 2), 2),
                round(_safe_float(params_dict.get("reference_grad_threshold", 15.0), 15.0), 4),
            ),
            (_safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55),),
        )
        if template_signature != self._cache_signature:
            self._cache_signature = template_signature
            self._match_cache.clear()

        result, diagnostics = run_locator_template_match(
            golden,
            frame,
            params_obj,
            thresholds_obj,
            roi,
            tool_id=str(tool_id),
            cache=self._match_cache,
        )
        self.last_diagnostics = diagnostics
        return result


def run_locator_template_match(
    golden: np.ndarray,
    frame: np.ndarray,
    params: Dict[str, Any] | ToolParams,
    thresholds: Dict[str, Any] | ToolThresholds,
    roi: Optional[ToolRoi | Dict[str, Any] | Sequence[int]],
    *,
    tool_id: str = "locator",
    cache: Optional[Dict[str, Any]] = None,
) -> Tuple[ToolRunResult, Dict[str, Any]]:
    """Run template matching locator core logic.

    Parameters
    ----------
    golden, frame:
        Golden reference and frame images. Only the first channel is used if
        images are multi-channel.
    params:
        Tool parameters or plain dictionary. Recognized keys are
        ``use_golden_crop`` (bool), ``template_roi`` (ROI descriptor),
        ``coarse_cap`` (int) and optional rotation settings
    thresholds:
        Threshold dictionary or :class:`ToolThresholds`. Uses
        ``threshold_corr`` if present.
    roi:
        Search ROI descriptor applied on the frame.

    Returns
    -------
    tuple
        ``(ToolRunResult, diagnostics)`` pair. Diagnostics include
        ``dx``, ``dy``, ``corr`` and affine transform ``T``.
    """

    import cv2
    import numpy as np
    from app.utils import imaging

    start_time = time.perf_counter()

    params_dict = (
        params.values if isinstance(params, ToolParams) else dict(params or {})
    )
    thresholds_dict = (
        thresholds.values
        if isinstance(thresholds, ToolThresholds)
        else dict(thresholds or {})
    )

    golden_u8 = _ensure_gray_u8(golden)
    frame_u8 = _ensure_gray_u8(frame)

    frame_h, frame_w = frame_u8.shape[:2]
    golden_h, golden_w = golden_u8.shape[:2]

    search_rect = _rect_from_any(roi)
    search_rect = _clamp_rect(search_rect, frame_w, frame_h)

    # A locator needs a smaller template inside a larger search area.  The
    # historical golden-crop fallback is retained only when explicitly set.
    use_golden_crop = bool(params_dict.get("use_golden_crop", False))
    template_source = None
    if not use_golden_crop:
        template_source = _rect_from_any(params_dict.get("template_roi"))
    elif template_source is None:
        template_source = _rect_from_any(search_rect)

    template_rect = _clamp_rect(template_source, golden_w, golden_h)

    coarse_cap = _safe_int(params_dict.get("coarse_cap", 600), 600)
    alignment_mode = str(params_dict.get("alignment_mode", "translation"))
    from app.models.recipe_contract import validate_locator_params
    validate_locator_params(params_dict)
    rotation_enabled = alignment_mode == "template_rotation"
    guided_edge = alignment_mode == "guided_edge"
    angle_range_deg = _safe_float(params_dict.get("angle_range_deg", 15.0), 15.0)
    angle_step_deg = _safe_float(params_dict.get("angle_step_deg", 1.0), 1.0)
    if not math.isfinite(angle_range_deg):
        angle_range_deg = 0.0
    if not math.isfinite(angle_step_deg) or abs(angle_step_deg) < 1e-6:
        angle_step_deg = 1.0
    angle_range_deg = max(0.0, abs(angle_range_deg))
    angle_step_deg = max(1e-3, abs(angle_step_deg))
    timings: list[imaging.TimeBlockResult] = []
    dx = 0.0
    dy = 0.0
    corr = 0.0
    theta_deg = 0.0
    used = 0
    reference_diagnostics: Dict[str, Any] = {}
    alignment_failure: Optional[str] = None
    match_quality: Dict[str, Any] = {}

    if template_rect is not None and search_rect is not None:
        tx, ty, tw, th = template_rect
        templ = golden_u8[ty : ty + th, tx : tx + tw]

        if templ.size > 0 and tw > 0 and th > 0:
            sx, sy, sw, sh = search_rect
            from app.services.locator_matching import match_shape

            template_descriptor = roi if use_golden_crop else params_dict.get("template_roi")
            template_mask = roi_shape_mask(template_descriptor, template_rect)
            if template_mask is None:
                template_mask = np.ones(templ.shape, dtype=np.uint8)
            template_mask = template_mask.astype(np.uint8)
            search_mask = roi_shape_mask(roi, search_rect)
            if search_mask is None:
                search_mask = np.ones((sh, sw), dtype=np.uint8)

            def shape_match(variant: np.ndarray, angle: float = 0.0):
                mask = template_mask
                if abs(angle) > 1e-6:
                    matrix = cv2.getRotationMatrix2D((tw / 2.0, th / 2.0), angle, 1.0)
                    matrix[0, 2] += variant.shape[1] / 2.0 - tw / 2.0
                    matrix[1, 2] += variant.shape[0] / 2.0 - th / 2.0
                    mask = cv2.warpAffine(
                        template_mask, matrix, (variant.shape[1], variant.shape[0]),
                        flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                    )
                return match_shape(
                    frame_u8[sy:sy+sh, sx:sx+sw], variant, mask, search_mask,
                    coarse_cap=max(1, coarse_cap),
                )

            def _enumerate_angles() -> list[float]:
                if not rotation_enabled or angle_range_deg <= 1e-6:
                    return [0.0]
                steps = int(math.floor(angle_range_deg / angle_step_deg + 1e-9))
                candidates = [round(idx * angle_step_deg, 6) for idx in range(-steps, steps + 1)]
                filtered = [
                    angle
                    for angle in candidates
                    if abs(angle) <= angle_range_deg + 1e-6
                ]
                if 0.0 not in filtered:
                    filtered.append(0.0)
                return sorted(set(filtered))

            angle_candidates = _enumerate_angles()
            rotated_cache: Optional[Dict[Any, np.ndarray]] = None
            base_key: Optional[tuple[int, tuple[int, int], str]] = None
            if cache is not None:
                rotated_cache = cache.setdefault("rotated_templates", {})
                base_key = (
                    int(templ.__array_interface__["data"][0]),
                    templ.shape[:2],
                    templ.dtype.str,
                )

            templates: list[tuple[float, np.ndarray]] = []
            max_w = tw
            max_h = th
            for angle in angle_candidates:
                templ_variant: np.ndarray
                if abs(angle) <= 1e-6:
                    templ_variant = templ
                else:
                    cache_key = None
                    if rotated_cache is not None and base_key is not None:
                        cache_key = (base_key, round(angle, 4))
                        cached = rotated_cache.get(cache_key)
                        if cached is not None:
                            templ_variant = cached
                        else:
                            cache_key = (base_key, round(angle, 4))
                            center = (tw / 2.0, th / 2.0)
                            M = cv2.getRotationMatrix2D(center, angle, 1.0)
                            cos_a = abs(M[0, 0])
                            sin_a = abs(M[0, 1])
                            new_w = max(1, int(math.ceil((th * sin_a) + (tw * cos_a))))
                            new_h = max(1, int(math.ceil((th * cos_a) + (tw * sin_a))))
                            M[0, 2] += (new_w / 2.0) - center[0]
                            M[1, 2] += (new_h / 2.0) - center[1]
                            templ_variant = cv2.warpAffine(
                                templ,
                                M,
                                (new_w, new_h),
                                flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT101,
                            )
                            rotated_cache[cache_key] = templ_variant
                    else:
                        center = (tw / 2.0, th / 2.0)
                        M = cv2.getRotationMatrix2D(center, angle, 1.0)
                        cos_a = abs(M[0, 0])
                        sin_a = abs(M[0, 1])
                        new_w = max(1, int(math.ceil((th * sin_a) + (tw * cos_a))))
                        new_h = max(1, int(math.ceil((th * cos_a) + (tw * sin_a))))
                        M[0, 2] += (new_w / 2.0) - center[0]
                        M[1, 2] += (new_h / 2.0) - center[1]
                        templ_variant = cv2.warpAffine(
                            templ,
                            M,
                            (new_w, new_h),
                            flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REFLECT101,
                        )
                templates.append((angle, templ_variant))
                max_w = max(max_w, templ_variant.shape[1])
                max_h = max(max_h, templ_variant.shape[0])

            extra_margin = int(math.ceil(max(max_w - tw, max_h - th) / 2.0))

            best_corr = -2.0
            best_angle = 0.0
            best_match: Optional[tuple[float, float]] = None
            best_size = (tw, th)
            best_used = 0

            for angle, templ_variant in templates:
                h_rot, w_rot = templ_variant.shape[:2]
                available_w = sw + 2 * extra_margin
                available_h = sh + 2 * extra_margin
                if available_w < w_rot or available_h < h_rot:
                    continue
                with imaging.time_block("match_template", timings):
                    dx_rel, dy_rel, corr_candidate, used_candidate, quality = shape_match(templ_variant, angle)

                match_x = float(sx + dx_rel)
                match_y = float(sy + dy_rel)
                if corr_candidate > best_corr:
                    best_corr = float(corr_candidate)
                    best_angle = float(angle)
                    best_match = (match_x, match_y)
                    best_size = (float(w_rot), float(h_rot))
                    best_used = int(used_candidate)
                    match_quality = quality

            if best_match is not None:
                corr = float(best_corr)
                used = int(best_used)
                # warpAffine's positive angle uses the opposite sign to
                # the image-coordinate transform consumed by the pipeline.
                theta_deg = -float(best_angle)
                match_x, match_y = best_match
                best_w, best_h = best_size
                cg_x = tx + (tw / 2.0)
                cg_y = ty + (th / 2.0)
                cf_x = match_x + (best_w / 2.0)
                cf_y = match_y + (best_h / 2.0)
                theta_rad = math.radians(theta_deg)
                cos_t = math.cos(theta_rad)
                sin_t = math.sin(theta_rad)
                dx = float(cf_x - (cos_t * cg_x - sin_t * cg_y))
                dy = float(cf_y - (sin_t * cg_x + cos_t * cg_y))

    if guided_edge:
        from app.services.tools.edge_profile_deviation import detect_guided_reference_edge

        def _reference_point(value: Any) -> Optional[tuple[float, float]]:
            if isinstance(value, dict) and {"x", "y"}.issubset(value):
                return float(value["x"]), float(value["y"])
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                return float(value[0]), float(value[1])
            return None

        reference_a = _reference_point(params_dict.get("reference_point_a"))
        reference_b = _reference_point(params_dict.get("reference_point_b"))
        required_coverage = min(1.0, max(0.0, _safe_float(
            params_dict.get("reference_min_coverage", 0.6), 0.6
        )))
        max_angle = max(0.0, _safe_float(
            params_dict.get("reference_max_angle_deg", 15.0), 15.0
        ))
        reference_diagnostics = {
            "required_coverage": required_coverage,
            "max_angle_deg": max_angle,
            "found": False,
        }
        if reference_a is None or reference_b is None or search_rect is None:
            reference_diagnostics["failure"] = "missing_reference_edge"
        else:
            # Template matching provides a coarse translation.  The A-B band
            # is then moved with it and measures the actual physical edge.
            coarse_a = (reference_a[0] + dx, reference_a[1] + dy)
            coarse_b = (reference_b[0] + dx, reference_b[1] + dy)
            try:
                with imaging.time_block("reference_edge", timings):
                    detection = detect_guided_reference_edge(
                        frame_u8,
                        search_rect,
                        coarse_a,
                        coarse_b,
                        blur_sigma=_safe_float(params_dict.get("reference_blur_sigma", 1.0), 1.0),
                        scan_step=max(1, _safe_int(params_dict.get("reference_scan_step", 2), 2)),
                        edge_polarity=str(params_dict.get("reference_edge_polarity", "any")),
                        grad_threshold=max(0.0, _safe_float(
                            params_dict.get("reference_grad_threshold", 15.0), 15.0
                        )),
                        search_half_window=max(1, _safe_int(
                            params_dict.get("reference_search_half_window", 20), 20
                        )),
                        outlier_trim_pct=0.1,
                        use_subpixel=bool(params_dict.get("reference_use_subpixel", False)),
                        valid_mask=roi_shape_mask(roi, search_rect),
                    )
                found_a = detection["point_a"]
                found_b = detection["point_b"]
                golden_angle = math.degrees(math.atan2(
                    reference_b[1] - reference_a[1], reference_b[0] - reference_a[0]
                ))
                found_angle = math.degrees(math.atan2(
                    found_b[1] - found_a[1], found_b[0] - found_a[0]
                ))
                theta_deg = ((found_angle - golden_angle + 180.0) % 360.0) - 180.0
                golden_mid = ((reference_a[0] + reference_b[0]) / 2.0,
                              (reference_a[1] + reference_b[1]) / 2.0)
                found_mid = ((found_a[0] + found_b[0]) / 2.0,
                             (found_a[1] + found_b[1]) / 2.0)
                theta_rad = math.radians(theta_deg)
                dx = found_mid[0] - (
                    math.cos(theta_rad) * golden_mid[0] - math.sin(theta_rad) * golden_mid[1]
                )
                dy = found_mid[1] - (
                    math.sin(theta_rad) * golden_mid[0] + math.cos(theta_rad) * golden_mid[1]
                )
                coverage = float(detection["coverage"])
                reference_diagnostics.update({
                    "found": coverage >= required_coverage and abs(theta_deg) <= max_angle,
                    "coverage": coverage,
                    "found_points": int(detection["found_points"]),
                    "scan_lines": int(detection["scan_lines"]),
                    "point_a": {"x": float(found_a[0]), "y": float(found_a[1])},
                    "point_b": {"x": float(found_b[0]), "y": float(found_b[1])},
                    "theta_deg": float(theta_deg),
                })
                if coverage < required_coverage:
                    reference_diagnostics["failure"] = "low_reference_coverage"
                elif abs(theta_deg) > max_angle:
                    reference_diagnostics["failure"] = "reference_angle_out_of_range"
            except (TypeError, ValueError) as exc:
                reference_diagnostics["failure"] = "reference_edge_not_found"
                reference_diagnostics["message"] = str(exc)

    max_shift_x = max(0.0, _safe_float(thresholds_dict.get("max_shift_x", 200.0), 200.0))
    max_shift_y = max(0.0, _safe_float(thresholds_dict.get("max_shift_y", 200.0), 200.0))
    if abs(dx) > max_shift_x:
        alignment_failure = "shift_x_out_of_range"
    elif abs(dy) > max_shift_y:
        alignment_failure = "shift_y_out_of_range"

    cos_theta = math.cos(math.radians(theta_deg))
    sin_theta = math.sin(math.radians(theta_deg))
    T = np.array([[cos_theta, -sin_theta, dx], [sin_theta, cos_theta, dy]], dtype=np.float32)

    found = bool(used > 0 and abs(float(corr)) > 1e-6)
    if guided_edge:
        found = found and bool(reference_diagnostics.get("found", False))
    found = found and alignment_failure is None

    metrics = {
        "dx": float(dx),
        "dy": float(dy),
        "theta_deg": float(theta_deg),
        "corr": float(corr),
        "match_attempts": float(used),
        "found": found,
    }
    if guided_edge:
        metrics["reference_coverage"] = float(reference_diagnostics.get("coverage", 0.0))
    metrics["template_contrast"] = float(match_quality.get("template_contrast", 0.0))
    second_corr = match_quality.get("second_corr")
    quality_warnings = []
    if metrics["template_contrast"] < 5.0:
        quality_warnings.append("Šablóna má nízky kontrast. Vyber výraznejší detail objektu.")
    if second_corr is not None:
        metrics["match_gap"] = float(corr - second_corr)
        if second_corr >= _safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55) and corr - second_corr < 0.05:
            quality_warnings.append("Podobná zhoda je aj na inom mieste. Zmenši oblasť hľadania alebo vyber jedinečnejšiu šablónu.")
    status = status_from_metrics("locator.template_match", metrics, thresholds_dict)

    diagnostics = {
        **metrics,
        "T": T,
        "status": status,
        "threshold_corr": _safe_float(thresholds_dict.get("threshold_corr", 0.55), 0.55),
        "max_shift_x": max_shift_x,
        "max_shift_y": max_shift_y,
        "alignment_mode": alignment_mode,
        "template_quality": match_quality,
        "quality_warnings": quality_warnings,
    }
    if guided_edge:
        diagnostics["reference_edge"] = reference_diagnostics
    if alignment_failure:
        diagnostics["alignment_failure"] = alignment_failure
    if timings:
        diagnostics["timings_ms"] = {
            entry.name: float(entry.elapsed_ms) for entry in timings
        }

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    diagnostics["latency_ms"] = latency_ms
    if alignment_failure or (guided_edge and not bool(reference_diagnostics.get("found", False))):
        status = "nok"
        diagnostics["status"] = status
    result = ToolRunResult(
        status=status,
        metrics={**metrics, "latency_ms": float(latency_ms)},
        latency_ms=float(latency_ms),
        debug_artifacts={
            "tool_id": tool_id,
            "type": "locator.template_match",
            "diagnostics": diagnostics,
        },
    )
    return result, diagnostics

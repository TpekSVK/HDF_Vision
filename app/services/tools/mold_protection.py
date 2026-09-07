"""Fail-closed empty-mould inspection built on the statistical presence model."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from app.models.schema import ToolParams, ToolThresholds
from app.services.tool_service import ToolRunResult
from app.services.tools.presence_absence_v2 import PresenceAbsenceV2Tool


class MoldProtectionV1Tool(PresenceAbsenceV2Tool):
    """Require the configured mould ROI to match its learned empty state."""

    def _run_pipeline(
        self,
        golden: np.ndarray,
        frame: np.ndarray,
        params: ToolParams,
        thresholds: ToolThresholds,
        context: Dict[str, Any],
    ) -> ToolRunResult:
        result = super()._run_pipeline(golden, frame, params, thresholds, context)
        metrics = dict(result.metrics or {})
        model_ready = bool(metrics.get("model_ready", False))
        valid_pixels = int(metrics.get("valid_pixel_count", 0) or 0)
        inspection_fault = not model_ready or valid_pixels <= 0
        residual_detected = model_ready and valid_pixels > 0 and result.status == "nok"

        if inspection_fault:
            result.status = "nok"
            reason = "model_not_ready" if not model_ready else "no_valid_pixels"
            metrics["decision_reason"] = reason
            inspection_state = "fault"
        elif residual_detected:
            inspection_state = "occupied"
        else:
            inspection_state = "empty"

        blobs = list(metrics.get("blobs", []) or [])
        largest = blobs[0] if residual_detected and blobs else None
        largest_dict = dict(largest) if isinstance(largest, dict) else {}
        metrics.update({
            "mold_empty": inspection_state == "empty",
            "residual_detected": bool(residual_detected),
            "residual_count": len(blobs) if residual_detected else 0,
            "candidate_count": len(blobs),
            "inspection_fault": bool(inspection_fault),
            "inspection_state": inspection_state,
            "residual_x": int(largest_dict.get("image_x", 0)),
            "residual_y": int(largest_dict.get("image_y", 0)),
            "residual_width": int(largest_dict.get("width", 0)),
            "residual_height": int(largest_dict.get("height", 0)),
        })
        result.metrics = metrics

        artifacts = dict(result.debug_artifacts or {})
        artifacts["type"] = "mold_protection_v1"
        if not residual_detected:
            artifacts["display_items"] = []
        diagnostics = dict(artifacts.get("diagnostics", {}) or {})
        diagnostics.update({
            "mold_empty": metrics["mold_empty"],
            "residual_detected": metrics["residual_detected"],
            "residual_count": metrics["residual_count"],
            "inspection_fault": metrics["inspection_fault"],
            "inspection_state": inspection_state,
            "decision_reason": metrics.get("decision_reason", ""),
        })
        if inspection_fault:
            diagnostics["message"] = (
                "Kontrola formy nie je pripravená. Zatvorenie formy musí zostať blokované."
            )
        elif residual_detected:
            diagnostics["message"] = "Vo forme bol nájdený zvyšný diel alebo iná anomália."
        else:
            diagnostics["message"] = "Kontrolovaná oblasť formy je prázdna."
        artifacts["diagnostics"] = diagnostics
        result.debug_artifacts = artifacts
        return result


__all__ = ["MoldProtectionV1Tool"]

"""Dataset compatibility, rebuilding and validation; no UI dependencies."""
from pathlib import Path
from typing import Any, Optional
import time
import numpy as np
from app.models.schema import Tool, ToolParams
from app.models.learning_contract import SAMPLE_PREPARATION_VERSION
from app.services.learning_context import samples_match
from app.services.roi_geometry import roi_local_exclusion_mask
from app.services.presence_absence_v2_service import (load_samples, load_model, build_model,
    save_model, ensure_assets_dirs, compute_roi_hash, evaluate_dataset, sensitivity_to_score_threshold)


class LearningDataset:
    @staticmethod
    def expected_shape(tool):
        rect = tool.roi.rect()
        return (int(rect[3]), int(rect[2])) if rect else None

    @staticmethod
    def ignore_mask(tool):
        return roi_local_exclusion_mask(tool.roi, tool.ignore_mask.value)

    def rebuild(
        self, tool: Tool, dirs: dict[str, Path], view_id: str, signature: str
    ) -> Optional[tuple[np.ndarray, np.ndarray, dict[str, float]]]:
        params = dict(tool.params.values or {})
        ok_samples = load_samples(dirs["ok"])
        nok_samples = load_samples(dirs["nok"])
        if (not samples_match(dirs["ok"].parent, signature)
                or params.get("sample_preparation_version") != SAMPLE_PREPARATION_VERSION
                or params.get("reference_model_invalidated")):
            raise ValueError("Vzorky patria starému alebo zmenenému nastaveniu. Resetujte učenie a zozbierajte nové vzorky.")
        try:
            median, mad, recommended, warnings, info = build_model(
                ok_samples,
                polarity=str(params.get("polarity", "any")),
                min_ok_samples=int(params.get("min_ok_samples", 15) or 15),
                expected_shape=self.expected_shape(tool),
                ignore_mask=self.ignore_mask(tool),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        stats = {
            "sample_preparation_version": SAMPLE_PREPARATION_VERSION,
            "learning_signature": signature,
            "model_method": "median_mad",
            "polarity": params.get("polarity", "any"),
            "created_at": int(time.time()),
            "sample_count_ok": info.used_ok_samples,
            "sample_count_nok": len(nok_samples),
            "recommended_thresholds": recommended,
            "warnings": warnings,
            "roi_hash": compute_roi_hash(tool.roi, tool.ignore_mask.value),
            "image_shape": list(median.shape),
            "ignored_ok_samples": info.ignored_ok_samples,
            "tool_type": tool.type,
            "tool_name": tool.name,
            "view_id": view_id,
        }
        save_model(dirs["model"], median, mad, stats)
        params.update(
            reference_model_ready=True,
            reference_model_needs_rebuild=False,
            reference_model_invalidated=False,
            roi_hash=stats["roi_hash"],
        )
        tool.params = ToolParams(params)
        return median, mad, recommended


    def evaluation_kwargs(
        self, tool: Tool, *, include_score: bool = True
    ) -> dict[str, Any]:
        thresholds = dict(tool.thresholds.values or {})
        params = dict(tool.params.values or {})
        values: dict[str, Any] = {
            "polarity": str(params.get("polarity", "any")),
            "total_area_threshold": float(thresholds.get("total_area_threshold", 50.0)),
            "min_blob_area": float(thresholds.get("min_blob_area", 10.0)),
            "ignore_mask": self.ignore_mask(tool),
            "max_blob_count": int(thresholds.get("max_blob_count", 0) or 0),
            "max_largest_blob_area": float(
                thresholds.get("max_largest_blob_area", 0.0) or 0.0
            ),
            "max_anomaly_area_percent": float(
                thresholds.get("max_anomaly_area_percent", 0.0) or 0.0
            ),
        }
        if include_score:
            values["score_threshold"] = (
                sensitivity_to_score_threshold(thresholds["sensitivity"])
                if "sensitivity" in thresholds
                else float(thresholds.get("score_threshold", 4.0))
            )
        return values


    def refresh(self, current, assets, signature):
        dirs = ensure_assets_dirs(assets)
        ok_samples = load_samples(dirs["ok"])
        nok_samples = load_samples(dirs["nok"])
        expected = self.expected_shape(current)
        compatible = sum(
            1 for sample in ok_samples
            if expected is None or tuple(np.asarray(sample).shape[:2]) == expected
        )
        params = dict(current.params.values or {})
        invalid = bool(params.get("reference_model_invalidated", False))
        model = load_model(dirs["model"])
        if (ok_samples or nok_samples) and (not signature or not samples_match(assets, signature)):
            invalid = True
        if model and not params.get("reference_model_needs_rebuild") and (not signature or model.stats.get("learning_signature") != signature):
            invalid = True
        if (ok_samples or nok_samples or model) and params.get("sample_preparation_version") != SAMPLE_PREPARATION_VERSION:
            invalid = True
        if model and not params.get("reference_model_needs_rebuild") and model.stats.get("sample_preparation_version") != SAMPLE_PREPARATION_VERSION:
            invalid = True
        params["reference_model_invalidated"] = invalid
        params["sample_count_ok"] = 0 if invalid else compatible
        params["sample_count_nok"] = 0 if invalid else len(nok_samples)
        params["reference_model_ready"] = bool(model) and not invalid and not params.get("reference_model_needs_rebuild", False)
        params["reference_assets_dir"] = str(assets)
        current.params = ToolParams(params)
        recommended = dict(model.stats.get("recommended_thresholds", {}) or {}) if model else {}
        return dict(compatible_ok=compatible if not invalid else 0,
                    nok_count=len(nok_samples) if not invalid else 0,
                    recommended=recommended)

    def validate(self, tool, dirs, signature):
        model = load_model(dirs["model"])
        if model is None:
            raise ValueError("Model nie je pripravený.")
        if (model.stats.get("sample_preparation_version") != SAMPLE_PREPARATION_VERSION
                or model.stats.get("learning_signature") != signature
                or not samples_match(dirs["ok"].parent, signature)
                or tool.params.values.get("reference_model_invalidated")
                or tool.params.values.get("reference_model_needs_rebuild")):
            raise ValueError("Model patrí starému alebo zmenenému nastaveniu. Vykonajte nové učenie.")
        ok_samples, nok_samples = load_samples(dirs["ok"]), load_samples(dirs["nok"])
        summary = evaluate_dataset(
            ok_samples, nok_samples, model.median, model.mad,
            **self.evaluation_kwargs(tool),
        )
        params = dict(tool.params.values or {})
        weak = (
            int(summary["ok_total"])
            < int(params.get("recommended_ok_samples", 30) or 30)
            or (0 < int(summary["nok_total"]) < 5)
        )
        return summary, weak


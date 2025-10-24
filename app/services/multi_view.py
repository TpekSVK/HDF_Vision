"""Helpers for working with multi-view recipes and aggregated verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Sequence, Tuple, Literal

import numpy as np

from app.models.schema import RecipeAggregationMode, RecipeV2
from app.services.compare_service import analyze
from app.services.storage_service import (
    load_multi_view_config,
    load_multi_view_step_assets,
)

StepStatus = Literal["ok", "nok", "warn"]


@dataclass(slots=True)
class StepVerdict:
    """Per-step verdict produced by the inspection pipeline."""

    step_id: str
    name: str
    status: StepStatus
    metrics: Mapping[str, Any]
    weight: float | None = None

    def normalized_weight(self, default: float = 1.0) -> float:
        try:
            if self.weight is None:
                return float(default)
            return max(0.0, float(self.weight))
        except (TypeError, ValueError):
            return float(default)


@dataclass(slots=True)
class AggregationResult:
    """Final verdict aggregated from per-step decisions."""

    mode: RecipeAggregationMode
    status: StepStatus
    per_step: Tuple[StepVerdict, ...]
    score: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "per_step": [
                {
                    "step_id": verdict.step_id,
                    "name": verdict.name,
                    "status": verdict.status,
                    "metrics": dict(verdict.metrics or {}),
                    **({"weight": verdict.weight} if verdict.weight is not None else {}),
                }
                for verdict in self.per_step
            ],
            "score": self.score,
        }


@dataclass(slots=True)
class MultiViewStepConfig:
    """Configuration bundle describing a single multi-view inspection step."""

    step_id: str
    name: str
    pose_enabled: bool
    settle_ms: int | None
    camera_profile: Mapping[str, Any]
    golden: np.ndarray | None
    regions: Sequence[Mapping[str, Any]]
    limits: Mapping[str, Any]


@dataclass(slots=True)
class MultiViewStepResult:
    """Execution result for a multi-view inspection step."""

    verdict: StepVerdict
    latency_ms: float
    frame: np.ndarray | None
    diagnostics: Mapping[str, Any] | None = None


@dataclass(slots=True)
class MultiViewRuntime:
    """Runtime-ready representation of a multi-view recipe."""

    steps: Tuple[MultiViewStepConfig, ...]
    aggregation_mode: RecipeAggregationMode
    weights: Mapping[str, float]
    fail_fast: bool = False

    def is_empty(self) -> bool:
        return not self.steps


@dataclass(slots=True)
class MultiViewRunResult:
    """Aggregated outcome of a multi-view inspection sequence."""

    steps: Tuple[MultiViewStepResult, ...]
    aggregation: AggregationResult
    cycle_time_ms: float
    fail_fast_triggered: bool = False


_STATUS_SCORE = {"ok": 1.0, "warn": 0.5, "nok": 0.0}


def _to_step_verdicts(entries: Iterable[Mapping[str, Any]]) -> Tuple[StepVerdict, ...]:
    normalized: list[StepVerdict] = []
    for entry in entries:
        status = str(entry.get("status", "ok")).lower()
        if status not in _STATUS_SCORE:
            status = "nok"
        normalized.append(
            StepVerdict(
                step_id=str(entry.get("step_id") or entry.get("id") or "step"),
                name=str(entry.get("name") or entry.get("step_id") or "Step"),
                status=status,  # type: ignore[arg-type]
                metrics=dict(entry.get("metrics", {}) or {}),
                weight=entry.get("weight"),
            )
        )
    return tuple(normalized)


def aggregate_step_verdicts(
    steps: Sequence[Mapping[str, Any] | StepVerdict],
    *,
    mode: RecipeAggregationMode = "AND",
    weights: Mapping[str, float] | None = None,
) -> AggregationResult:
    """Aggregate per-step verdicts according to the selected mode."""

    if not steps:
        empty_verdict = StepVerdict(step_id="step-0", name="Step", status="ok", metrics={})
        return AggregationResult(mode=mode, status="ok", per_step=(empty_verdict,), score=1.0)

    if all(isinstance(step, StepVerdict) for step in steps):
        step_verdicts = tuple(
            step if isinstance(step, StepVerdict) else StepVerdict(**step) for step in steps
        )  # type: ignore[misc]
    else:
        step_verdicts = _to_step_verdicts(steps)  # type: ignore[arg-type]

    normalized_mode = str(mode or "AND").upper()
    if normalized_mode not in {"AND", "OR", "WEIGHTED"}:
        normalized_mode = "AND"

    if normalized_mode == "AND":
        statuses = [step.status for step in step_verdicts]
        if any(status == "nok" for status in statuses):
            final = "nok"
        elif any(status == "warn" for status in statuses):
            final = "warn"
        else:
            final = "ok"
        return AggregationResult(mode="AND", status=final, per_step=step_verdicts, score=None)

    if normalized_mode == "OR":
        statuses = [step.status for step in step_verdicts]
        if any(status == "ok" for status in statuses):
            final = "ok"
        elif any(status == "warn" for status in statuses):
            final = "warn"
        else:
            final = "nok"
        return AggregationResult(mode="OR", status=final, per_step=step_verdicts, score=None)

    weights_map = {str(key): float(value) for key, value in dict(weights or {}).items() if value is not None}
    weighted_sum = 0.0
    weight_total = 0.0
    for step in step_verdicts:
        step_weight = step.normalized_weight(weights_map.get(step.step_id, 1.0))
        score = _STATUS_SCORE.get(step.status, 0.0)
        weighted_sum += step_weight * score
        weight_total += step_weight

    if weight_total <= 0.0:
        final_status = "nok" if any(step.status == "nok" for step in step_verdicts) else "warn"
        return AggregationResult(mode="WEIGHTED", status=final_status, per_step=step_verdicts, score=0.0)

    normalized_score = weighted_sum / weight_total
    if normalized_score >= 0.75:
        final_status = "ok"
    elif normalized_score >= 0.25:
        final_status = "warn"
    else:
        final_status = "nok"

    return AggregationResult(
        mode="WEIGHTED",
        status=final_status,
        per_step=step_verdicts,
        score=float(normalized_score),
    )


def load_multi_view_runtime(
    recipe_name: str,
    recipe: RecipeV2 | None = None,
    *,
    base_dir: str | Path = "/data",
) -> MultiViewRuntime:
    """Load multi-view configuration and assets into a runtime-friendly structure."""

    base_path = Path(base_dir)
    config = load_multi_view_config(recipe_name, base_dir=base_path)
    aggregation = str(config.get("aggregation", getattr(recipe, "aggregation", "AND"))).upper()
    if aggregation not in {"AND", "OR", "WEIGHTED"}:
        aggregation = "AND"

    weights: MutableMapping[str, float] = {}
    if recipe is not None:
        for key, value in dict(getattr(recipe, "aggregation_weights", {}) or {}).items():
            try:
                weights[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

    runtime_steps: list[MultiViewStepConfig] = []
    for entry in config.get("steps", []) or []:
        step_id = str(entry.get("id") or entry.get("step_id") or "").strip() or "step"
        name = str(entry.get("name") or step_id)
        pose_enabled = bool(entry.get("pose_enabled", getattr(recipe, "pose_enabled", True)))
        settle_raw = entry.get("settle_ms")
        try:
            settle_ms = None if settle_raw in (None, "") else max(0, int(settle_raw))
        except Exception:
            settle_ms = None
        camera_profile = entry.get("camera_profile") or {}
        if not isinstance(camera_profile, Mapping):
            camera_profile = {}

        assets = load_multi_view_step_assets(recipe_name, step_id, base_dir=base_path)
        golden = assets.get("golden")
        if isinstance(golden, np.ndarray):
            golden = np.asarray(golden)
        else:
            golden = None
        regions = list(assets.get("regions", []) or [])
        limits = dict(assets.get("limits", {}) or {})

        runtime_steps.append(
            MultiViewStepConfig(
                step_id=step_id,
                name=name,
                pose_enabled=pose_enabled,
                settle_ms=settle_ms,
                camera_profile=dict(camera_profile),
                golden=golden,
                regions=regions,
                limits=limits,
            )
        )

    fail_fast = bool(config.get("fail_fast", False))

    return MultiViewRuntime(
        steps=tuple(runtime_steps),
        aggregation_mode=aggregation,  # type: ignore[arg-type]
        weights=dict(weights),
        fail_fast=fail_fast,
    )


def run_multi_view_sequence(
    runtime: MultiViewRuntime,
    *,
    capture: Callable[[MultiViewStepConfig], np.ndarray | None],
    fail_fast: bool | None = None,
) -> MultiViewRunResult:
    """Execute the configured multi-view sequence using the provided capture callback."""

    if runtime.is_empty():
        empty_verdict = StepVerdict(step_id="step-0", name="Step", status="ok", metrics={})
        aggregation = AggregationResult(
            mode=runtime.aggregation_mode,
            status="ok",
            per_step=(empty_verdict,),
            score=1.0,
        )
        return MultiViewRunResult(
            steps=(MultiViewStepResult(verdict=empty_verdict, latency_ms=0.0, frame=None),),
            aggregation=aggregation,
            cycle_time_ms=0.0,
        )

    effective_fail_fast = runtime.fail_fast if fail_fast is None else bool(fail_fast)

    executed: list[MultiViewStepResult] = []
    fail_fast_triggered = False
    sequence_start = time.perf_counter()

    for step_config in runtime.steps:
        step_start = time.perf_counter()
        captured_frame: np.ndarray | None = None
        step_metrics: Dict[str, Any] = {}
        diagnostics: Dict[str, Any] = {}
        status: StepStatus = "nok"

        try:
            captured_frame = capture(step_config)
        except Exception as exc:  # pragma: no cover - defensive guard
            step_metrics["error"] = str(exc)
            captured_frame = None

        if captured_frame is None:
            step_metrics.setdefault("error", "frame_missing")
            status = "nok"
        elif step_config.golden is None:
            step_metrics["error"] = "missing_golden"
            status = "nok"
        else:
            try:
                frame_array = np.asarray(captured_frame)
                golden_array = np.asarray(step_config.golden)
                result = analyze(
                    golden_array,
                    list(step_config.regions or []),
                    frame_array,
                    dict(step_config.limits or {}),
                    pose_enabled=bool(step_config.pose_enabled),
                )
                metrics = dict(result.get("metrics", {}) or {})
                diagnostics_payload = result.get("diagnostics")
                if isinstance(diagnostics_payload, Mapping):
                    diagnostics.update(diagnostics_payload)
                score_value = result.get("score")
                if score_value is not None:
                    try:
                        metrics.setdefault("score", float(score_value))
                    except (TypeError, ValueError):
                        pass
                status = "ok" if bool(result.get("ok", True)) else "nok"
                step_metrics.update(metrics)
            except Exception as exc:
                step_metrics["error"] = str(exc)
                status = "nok"

        latency_ms = (time.perf_counter() - step_start) * 1000.0
        step_metrics.setdefault("latency_ms", float(latency_ms))

        verdict = StepVerdict(
            step_id=step_config.step_id,
            name=step_config.name,
            status=status,
            metrics=dict(step_metrics),
            weight=runtime.weights.get(step_config.step_id),
        )

        executed.append(
            MultiViewStepResult(
                verdict=verdict,
                latency_ms=float(latency_ms),
                frame=None if captured_frame is None else np.asarray(captured_frame),
                diagnostics=diagnostics or None,
            )
        )

        if effective_fail_fast and status == "nok":
            fail_fast_triggered = True
            break

    aggregation = aggregate_step_verdicts(
        [result.verdict for result in executed],
        mode=runtime.aggregation_mode,
        weights=runtime.weights,
    )

    cycle_time_ms = (time.perf_counter() - sequence_start) * 1000.0

    return MultiViewRunResult(
        steps=tuple(executed),
        aggregation=aggregation,
        cycle_time_ms=float(cycle_time_ms),
        fail_fast_triggered=fail_fast_triggered,
    )


__all__ = [
    "AggregationResult",
    "MultiViewRuntime",
    "MultiViewRunResult",
    "MultiViewStepConfig",
    "MultiViewStepResult",
    "StepStatus",
    "StepVerdict",
    "aggregate_step_verdicts",
    "load_multi_view_runtime",
    "run_multi_view_sequence",
]

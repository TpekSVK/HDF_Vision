"""Helpers for working with multi-view recipes and aggregated verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple, Literal

from app.models.schema import RecipeAggregationMode

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
        step_verdicts = tuple(step if isinstance(step, StepVerdict) else StepVerdict(**step) for step in steps)  # type: ignore[misc]
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

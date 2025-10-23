"""Multi-view orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Sequence

import imageio.v3 as iio
import numpy as np

from app.models.schema import MultiViewConfig, MultiViewStep, RecipeV2
from app.services.compare_service import analyze


@dataclass(slots=True)
class MultiViewStepRun:
    """Result of executing a single multi-view step."""

    step: MultiViewStep
    index: int
    status: Literal["ok", "nok", "skipped", "error"] = "skipped"
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    executed: bool = False

    def verdict_bool(self) -> bool:
        return self.status == "ok"


@dataclass(slots=True)
class MultiViewRunResult:
    """Aggregated multi-view execution summary."""

    steps: List[MultiViewStepRun]
    verdict: Literal["ok", "nok"]
    aggregation: Literal["AND", "OR", "WEIGHTED"]
    score: float
    fail_fast_triggered: bool = False

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"


class MultiViewRunner:
    """Execute recipe steps with per-step goldens and aggregation."""

    def __init__(self, *, base_dir: str | Path = "/data") -> None:
        self.base = Path(base_dir)

    def _step_golden_path(self, recipe_name: str, step: MultiViewStep) -> Path:
        return self.base / "recipes" / recipe_name / step.golden_path

    def _load_step_golden(self, recipe_name: str, step: MultiViewStep) -> np.ndarray:
        path = self._step_golden_path(recipe_name, step)
        if not path.exists():
            raise FileNotFoundError(f"Golden not found for step '{step.step_id}' at {path}")
        image = iio.imread(path)
        if image.ndim == 3:
            image = image[:, :, 0]
        return np.asarray(image, dtype=np.uint8)

    def _aggregate(self, config: MultiViewConfig, steps: Sequence[MultiViewStepRun]) -> tuple[str, float]:
        executed = [step for step in steps if step.status != "skipped"]
        if not executed:
            return "ok", 1.0

        if config.aggregation == "AND":
            verdict = "ok" if all(step.status == "ok" for step in executed) else "nok"
            return verdict, 1.0 if verdict == "ok" else 0.0

        if config.aggregation == "OR":
            verdict = "ok" if any(step.status == "ok" for step in executed) else "nok"
            return verdict, 1.0 if verdict == "ok" else 0.0

        weights = config.effective_weights(step.step.step_id for step in executed)
        total_weight = sum(max(weight, 0.0) for weight in weights.values())
        if total_weight <= 0.0:
            total_weight = float(len(executed))
            weights = {step.step.step_id: 1.0 for step in executed}
        passed_weight = sum(
            weights.get(step.step.step_id, 0.0)
            for step in executed
            if step.status == "ok"
        )
        score = passed_weight / total_weight if total_weight > 0 else 0.0
        verdict = "ok" if score >= config.weighted_threshold else "nok"
        return verdict, float(score)

    def run(
        self,
        recipe_name: str,
        frames: Sequence[np.ndarray],
        recipe: RecipeV2,
        *,
        fail_fast: bool = False,
    ) -> MultiViewRunResult:
        config: MultiViewConfig = recipe.multi_view.copy()
        ordered_steps = list(config.iter_steps())
        results: List[MultiViewStepRun] = []
        aborted = False

        for index, step in enumerate(ordered_steps):
            if aborted:
                results.append(MultiViewStepRun(step=step.copy(), index=index, status="skipped"))
                continue

            frame = frames[index] if index < len(frames) else None
            if frame is None:
                results.append(
                    MultiViewStepRun(
                        step=step.copy(),
                        index=index,
                        status="error",
                        error="frame_missing",
                        executed=False,
                    )
                )
                aborted = fail_fast
                continue

            try:
                golden = self._load_step_golden(recipe_name, step)
                analysis = analyze(
                    golden,
                    step.regions,
                    np.asarray(frame),
                    step.thresholds.to_dict(),
                    pose_enabled=bool(step.pose_enabled),
                )
                ok = bool(analysis.get("ok", False))
                metrics = dict(analysis.get("metrics", {}))
                status: Literal["ok", "nok"] = "ok" if ok else "nok"
                result = MultiViewStepRun(
                    step=step.copy(),
                    index=index,
                    status=status,
                    metrics=metrics,
                    executed=True,
                )
            except Exception as exc:  # pragma: no cover - defensive
                result = MultiViewStepRun(
                    step=step.copy(),
                    index=index,
                    status="error",
                    error=str(exc),
                    executed=True,
                )

            results.append(result)

            if fail_fast and result.status == "nok":
                aborted = True

        verdict, score = self._aggregate(config, results)
        return MultiViewRunResult(
            steps=results,
            verdict=verdict,  # type: ignore[arg-type]
            aggregation=config.aggregation,
            score=score,
            fail_fast_triggered=aborted,
        )


__all__ = [
    "MultiViewRunner",
    "MultiViewRunResult",
    "MultiViewStepRun",
]

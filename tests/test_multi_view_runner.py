import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")

import imageio.v3 as iio

from app.models.schema import MultiViewConfig, MultiViewStep, RecipeV2, ToolThresholds
from app.services.multi_view_service import MultiViewRunner


def _create_step(
    recipe_dir: Path,
    idx: int,
    golden: np.ndarray,
    *,
    ssim_min: float = 0.0,
) -> MultiViewStep:
    recipe_dir.mkdir(parents=True, exist_ok=True)
    filename = f"step_{idx}.png"
    path = recipe_dir / filename
    iio.imwrite(path, golden, extension=".png")
    height, width = golden.shape[:2]
    return MultiViewStep(
        step_id=f"step_{idx}",
        name=f"Step {idx}",
        order=idx,
        golden_path=filename,
        pose_enabled=False,
        regions=[{"reg_type": "roi", "shape": "rect", "geom": [0, 0, width, height]}],
        thresholds=ToolThresholds({"ssim_min": float(ssim_min)}),
    )


def test_multi_view_and_aggregation(tmp_path: Path) -> None:
    recipe_name = "mv_recipe"
    recipe_dir = tmp_path / "recipes" / recipe_name

    golden_ok = np.zeros((32, 32), dtype=np.uint8)
    golden_nok = np.zeros_like(golden_ok)
    step1 = _create_step(recipe_dir, 0, golden_ok, ssim_min=0.1)
    step2 = _create_step(recipe_dir, 1, golden_nok, ssim_min=0.95)

    frames = [golden_ok.copy(), np.full_like(golden_nok, 200)]

    recipe = RecipeV2()
    recipe.multi_view = MultiViewConfig(steps=[step1, step2], aggregation="AND")

    runner = MultiViewRunner(base_dir=tmp_path)
    result = runner.run(recipe_name, frames, recipe)

    assert result.verdict == "nok"
    assert result.steps[0].status == "ok"
    assert result.steps[1].status == "nok"

    recipe.multi_view.aggregation = "OR"
    result_or = runner.run(recipe_name, frames, recipe)
    assert result_or.verdict == "ok"

    recipe.multi_view = MultiViewConfig(
        steps=[step1, step2],
        aggregation="WEIGHTED",
        weights={"step_0": 0.7, "step_1": 0.3},
        weighted_threshold=0.5,
    )
    result_weighted = runner.run(recipe_name, frames, recipe)
    assert result_weighted.verdict == "ok"
    assert result_weighted.score >= 0.5


def test_multi_view_fail_fast(tmp_path: Path) -> None:
    recipe_name = "fail_fast"
    recipe_dir = tmp_path / "recipes" / recipe_name

    golden = np.zeros((16, 16), dtype=np.uint8)
    step1 = _create_step(recipe_dir, 0, golden, ssim_min=0.1)
    step2 = _create_step(recipe_dir, 1, golden, ssim_min=0.9)

    frames = [np.full_like(golden, 50), np.zeros_like(golden)]

    recipe = RecipeV2()
    recipe.multi_view = MultiViewConfig(steps=[step1, step2], aggregation="AND")

    runner = MultiViewRunner(base_dir=tmp_path)
    result = runner.run(recipe_name, frames, recipe, fail_fast=True)

    assert result.verdict == "nok"
    assert result.fail_fast_triggered is True
    assert result.steps[0].status in {"ok", "nok"}
    assert result.steps[1].status == "skipped"

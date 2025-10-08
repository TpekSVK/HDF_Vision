import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")

from app.models.schema import RecipeV2, Tool, ToolParams, ToolThresholds, ToolRoi
from app.services.tool_service import ToolRunResult, run_pipeline
from app.utils import imaging


def _make_recipe(apply_alignment: bool) -> RecipeV2:
    locator = Tool(
        type="locator.template_match",
        name="locator",
        enabled=True,
        order=1,
        roi=ToolRoi({"x": 0, "y": 0, "w": 32, "h": 32}),
        params=ToolParams(
            {
                "use_golden_crop": True,
                "coarse_cap": 64,
                "apply_alignment": apply_alignment,
            }
        ),
        thresholds=ToolThresholds({"threshold_corr": 0.0}),
    )

    ssim_tool = Tool(
        type="ssim",
        name="ssim",
        enabled=True,
        order=2,
        roi=ToolRoi({"x": 0, "y": 0, "w": 32, "h": 32}),
        params=ToolParams({}),
        thresholds=ToolThresholds({"ssim_min": 0.0}),
    )

    return RecipeV2(tools=[locator, ssim_tool])


def _make_test_images() -> tuple[np.ndarray, np.ndarray]:
    golden = np.zeros((32, 32), dtype=np.uint8)
    block = np.arange(36, dtype=np.uint8).reshape(6, 6) * 3
    golden[10:16, 13:19] = block

    frame = imaging.warp_by_translation_u8(golden, 3.0, -2.0)
    return golden, frame


def test_locator_updates_context_with_alignment() -> None:
    golden, frame = _make_test_images()
    recipe = _make_recipe(apply_alignment=True)

    context, diagnostics, results = run_pipeline(recipe, golden, frame)

    locator_diag = diagnostics[0]
    dx = locator_diag["dx"]
    dy = locator_diag["dy"]
    expected_T = locator_diag["T"]

    assert results
    assert isinstance(results[0], ToolRunResult)
    assert results[0].status == "ok"
    assert results[0].metrics["dx"] == pytest.approx(dx)
    assert results[0].metrics["dy"] == pytest.approx(dy)

    assert context.T_total is not None
    assert np.allclose(context.T_total, expected_T)

    expected_aligned = imaging.warp_by_translation_u8(frame, -dx, -dy)
    assert context.frame_aligned is not None
    assert np.allclose(context.frame_aligned, expected_aligned)


def test_locator_keeps_frame_when_alignment_disabled() -> None:
    golden, frame = _make_test_images()
    recipe = _make_recipe(apply_alignment=False)

    context, diagnostics, results = run_pipeline(recipe, golden, frame)

    locator_diag = diagnostics[0]
    expected_T = locator_diag["T"]

    assert results
    assert results[0].status == "ok"

    assert context.T_total is not None
    assert np.allclose(context.T_total, expected_T)
    assert context.frame_aligned is frame


def test_ssim_benefits_from_aligned_frame() -> None:
    golden, frame = _make_test_images()
    recipe = _make_recipe(apply_alignment=True)

    context, _, _ = run_pipeline(recipe, golden, frame)

    assert context.frame_aligned is not None
    ssim_original = imaging.ssim_u8(golden, frame)
    ssim_aligned = imaging.ssim_u8(golden, context.frame_aligned)

    assert ssim_aligned >= ssim_original
    assert ssim_aligned > 0.99

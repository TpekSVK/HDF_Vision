import sys
from pathlib import Path

import math
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("cv2")
import cv2

from app.models.schema import RecipeV2, Tool, ToolParams, ToolThresholds, ToolRoi
from app.services.tool_service import run_pipeline
from app.utils import imaging


def _make_recipe(
    apply_alignment: bool,
    *,
    threshold_corr: float = 0.0,
    ssim_min: float = 0.0,
    rotation_enabled: bool = False,
    angle_range_deg: float = 15.0,
    angle_step_deg: float = 1.0,
) -> RecipeV2:
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
                "rotation_enabled": rotation_enabled,
                "angle_range_deg": angle_range_deg,
                "angle_step_deg": angle_step_deg,
            }
        ),
        thresholds=ToolThresholds({"threshold_corr": threshold_corr}),
    )

    ssim_tool = Tool(
        type="ssim",
        name="ssim",
        enabled=True,
        order=2,
        roi=ToolRoi({"x": 0, "y": 0, "w": 32, "h": 32}),
        params=ToolParams({}),
        thresholds=ToolThresholds({"ssim_min": ssim_min}),
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

    pipeline = run_pipeline(golden, frame, recipe)
    context = pipeline.context
    diagnostics = pipeline.diagnostics
    results = pipeline.per_tool

    locator_diag = diagnostics[0]
    dx = locator_diag["dx"]
    dy = locator_diag["dy"]
    assert locator_diag["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    expected_T = locator_diag["T"]

    assert results
    assert results[0].status == "ok"
    assert results[0].metrics["dx"] == pytest.approx(dx)
    assert results[0].metrics["dy"] == pytest.approx(dy)
    assert results[0].metrics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    assert "latency_ms" in results[0].metrics
    assert locator_diag["latency_ms"] >= 0.0

    assert context.T_total is not None
    assert np.allclose(context.T_total, expected_T)

    expected_aligned = imaging.warp_by_translation_u8(frame, -dx, -dy)
    assert context.frame_aligned is not None
    assert np.allclose(context.frame_aligned, expected_aligned)

    assert pipeline.overlay_items
    assert all(report.overlay_items for report in pipeline.per_tool)


def test_locator_keeps_frame_when_alignment_disabled() -> None:
    golden, frame = _make_test_images()
    recipe = _make_recipe(apply_alignment=False)

    pipeline = run_pipeline(golden, frame, recipe)
    context = pipeline.context
    diagnostics = pipeline.diagnostics
    results = pipeline.per_tool

    locator_diag = diagnostics[0]
    expected_T = locator_diag["T"]
    assert locator_diag["theta_deg"] == pytest.approx(0.0, abs=1e-3)

    assert results
    assert results[0].status == "ok"
    assert "latency_ms" in results[0].metrics
    assert results[0].metrics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    assert locator_diag["latency_ms"] >= 0.0

    assert context.T_total is not None
    assert np.allclose(context.T_total, expected_T)
    assert context.frame_aligned is frame


def test_ssim_benefits_from_aligned_frame() -> None:
    golden, frame = _make_test_images()
    recipe = _make_recipe(apply_alignment=True)

    pipeline = run_pipeline(golden, frame, recipe)
    context = pipeline.context

    assert context.frame_aligned is not None
    ssim_original = imaging.ssim_u8(golden, frame)
    ssim_aligned = imaging.ssim_u8(golden, context.frame_aligned)

    assert ssim_aligned >= ssim_original
    assert ssim_aligned > 0.99


def test_pipeline_alignment_modes_produce_consistent_ssim() -> None:
    golden, frame = _make_test_images()

    recipe_aligned = _make_recipe(apply_alignment=True, ssim_min=0.95)
    pipeline_a = run_pipeline(golden, frame, recipe_aligned)
    context_a = pipeline_a.context
    diagnostics_a = pipeline_a.diagnostics
    results_a = pipeline_a.per_tool

    recipe_virtual = _make_recipe(apply_alignment=False, ssim_min=0.95)
    pipeline_b = run_pipeline(golden, frame, recipe_virtual)
    context_b = pipeline_b.context
    diagnostics_b = pipeline_b.diagnostics
    results_b = pipeline_b.per_tool

    assert pipeline_a.policy_applied is None
    assert pipeline_b.policy_applied is None

    assert context_a.frame_is_aligned is True
    assert context_b.frame_is_aligned is False
    assert context_b.frame_aligned is frame

    locator_a = results_a[0]
    locator_b = results_b[0]

    assert locator_a.metrics["dx"] == pytest.approx(3.0, abs=1.0)
    assert locator_a.metrics["dy"] == pytest.approx(-2.0, abs=1.0)
    assert locator_b.metrics["dx"] == pytest.approx(3.0, abs=1.0)
    assert locator_b.metrics["dy"] == pytest.approx(-2.0, abs=1.0)
    assert locator_a.metrics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    assert locator_b.metrics["theta_deg"] == pytest.approx(0.0, abs=1e-3)
    assert "latency_ms" in locator_a.metrics
    assert "latency_ms" in locator_b.metrics

    ssim_a = results_a[1].metrics["ssim"]
    ssim_b = results_b[1].metrics["ssim"]
    assert ssim_a > 0.99
    assert ssim_b > 0.99
    assert ssim_a == pytest.approx(ssim_b, abs=1e-4)
    assert "latency_ms" in results_a[1].metrics
    assert "latency_ms" in results_b[1].metrics

    assert diagnostics_a[1]["virtual_alignment"] is False
    assert diagnostics_b[1]["virtual_alignment"] is True


def test_pipeline_reports_nok_when_correlation_is_low() -> None:
    golden = np.zeros((32, 32), dtype=np.uint8)
    golden[8:24, 8:24] = 200
    frame = np.zeros_like(golden)

    recipe = _make_recipe(apply_alignment=True, threshold_corr=0.9, ssim_min=0.9)
    recipe.on_locator_failure = "continue_without_alignment"
    pipeline = run_pipeline(golden, frame, recipe)
    context = pipeline.context
    diagnostics = pipeline.diagnostics
    results = pipeline.per_tool

    locator_result = results[0]
    assert results
    locator_result = results[0]
    assert locator_result.status == "nok"
    assert diagnostics[0]["status"] == "nok"
    assert locator_result.metrics["corr"] == pytest.approx(0.0)
    assert locator_result.metrics.get("found") is False
    assert "latency_ms" in locator_result.metrics
    assert diagnostics[0]["latency_ms"] >= 0.0
    assert pipeline.policy_applied == "continue_without_alignment"
    assert diagnostics[0].get("policy_applied") == "continue_without_alignment"
    assert diagnostics[0].get("locator_failure") is True


def test_pipeline_handles_locator_rotation() -> None:
    golden = np.zeros((32, 32), dtype=np.uint8)
    block = np.arange(64, dtype=np.uint8).reshape(8, 8)
    golden[10:18, 12:20] = block

    center = (golden.shape[1] / 2.0, golden.shape[0] / 2.0)
    angle_deg = 9.0
    rot = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    frame = cv2.warpAffine(
        golden,
        rot,
        golden.shape[::-1],
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT101,
    )

    recipe = _make_recipe(
        apply_alignment=True,
        rotation_enabled=True,
        angle_range_deg=12.0,
        angle_step_deg=1.0,
        ssim_min=0.9,
    )

    pipeline = run_pipeline(golden, frame, recipe)
    locator_report = pipeline.per_tool[0]
    locator_diag = pipeline.diagnostics[0]

    assert locator_report.metrics["theta_deg"] == pytest.approx(angle_deg, abs=1.0)
    assert locator_diag["theta_deg"] == pytest.approx(angle_deg, abs=1.0)
    assert pipeline.context.frame_is_aligned is True
    assert pipeline.context.frame_aligned is not None

    cos_t = math.cos(math.radians(locator_diag["theta_deg"]))
    sin_t = math.sin(math.radians(locator_diag["theta_deg"]))
    expected_T = np.array(
        [[cos_t, -sin_t, locator_report.metrics["dx"]], [sin_t, cos_t, locator_report.metrics["dy"]]],
        dtype=np.float32,
    )
    assert pipeline.context.T_total is not None
    assert np.allclose(pipeline.context.T_total, expected_T, atol=0.2)

    ssim_metric = pipeline.per_tool[1].metrics["ssim"]
    assert ssim_metric > 0.9

    assert len(results) == 2
    ssim_result = results[1]
    assert ssim_result.status == "nok"
    assert ssim_result.metrics["ssim"] < 0.5
    assert "latency_ms" in ssim_result.metrics
    assert diagnostics[1]["virtual_alignment"] is False

    assert context.frame_is_aligned is False
    assert np.allclose(context.T_total, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))


def test_pipeline_without_locator_uses_identity_transform() -> None:
    golden = np.zeros((16, 16), dtype=np.uint8)
    frame = golden.copy()

    ssim_tool = Tool(
        type="ssim",
        name="ssim",
        enabled=True,
        order=0,
        roi=ToolRoi({"x": 0, "y": 0, "w": 16, "h": 16}),
        params=ToolParams({}),
        thresholds=ToolThresholds({}),
    )

    recipe = RecipeV2(tools=[ssim_tool])
    pipeline = run_pipeline(golden, frame, recipe)

    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    assert np.allclose(pipeline.context.T_total, identity)
    assert pipeline.context.frame_is_aligned is False
    assert pipeline.context.frame_aligned is frame
    assert len(pipeline.per_tool) == 1
    assert pipeline.per_tool[0].status == "ok"


def test_pipeline_locator_failure_policy_fail_stops_execution() -> None:
    golden = np.zeros((32, 32), dtype=np.uint8)
    golden[8:24, 8:24] = 200
    frame = np.zeros_like(golden)

    recipe = _make_recipe(apply_alignment=True, threshold_corr=0.9, ssim_min=0.9)
    recipe.on_locator_failure = "fail"

    pipeline = run_pipeline(golden, frame, recipe)

    assert pipeline.status == "nok"
    assert pipeline.policy_applied == "fail"
    assert len(pipeline.per_tool) == 1
    assert pipeline.per_tool[0].tool.type.startswith("locator")
    assert pipeline.diagnostics[0]["status"] == "nok"
    assert pipeline.diagnostics[0].get("policy_applied") == "fail"
    assert np.allclose(
        pipeline.context.T_total,
        np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
    )
    assert pipeline.context.frame_is_aligned is False


def test_pipeline_enforces_locator_first_even_when_order_swapped() -> None:
    golden, frame = _make_test_images()

    locator = Tool(
        type="locator.template_match",
        name="locator",
        enabled=True,
        order=2,
        roi=ToolRoi({"x": 0, "y": 0, "w": 32, "h": 32}),
        params=ToolParams({"use_golden_crop": True}),
        thresholds=ToolThresholds({}),
    )

    ssim_tool = Tool(
        type="ssim",
        name="ssim",
        enabled=True,
        order=1,
        roi=ToolRoi({"x": 0, "y": 0, "w": 32, "h": 32}),
        params=ToolParams({}),
        thresholds=ToolThresholds({}),
    )

    recipe = RecipeV2(tools=[ssim_tool, locator])
    pipeline = run_pipeline(golden, frame, recipe)

    assert pipeline.per_tool
    assert pipeline.per_tool[0].tool.type.startswith("locator")
    assert pipeline.diagnostics[0]["type"].startswith("locator")

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

pytest.importorskip("cv2")

from app.models.schema import (
    CameraProfile,
    RecipeStep,
    RecipeV2,
    Tool,
    ToolParams,
    ToolThresholds,
)
from app.services.multi_view import (
    MultiViewRuntime,
    MultiViewStepConfig,
    aggregate_step_verdicts,
    load_multi_view_runtime,
    run_multi_view_sequence,
    StepVerdict,
)

import imageio.v3 as iio
import numpy as np


def test_recipe_step_from_dict_normalizes_fields() -> None:
    raw = {
        "id": "01",
        "name": "Top View",
        "golden_image": "golden_top.png",
        "pose_enabled": False,
        "regions": [{"x": 0, "y": 0, "w": 10, "h": 10}],
        "tools": [
            {
                "type": "ssim",
                "name": "ssim",
                "order": 5,
                "params": {"foo": "bar"},
            }
        ],
        "thresholds": {"ssim_min": 0.9},
        "camera_profile": {"resolution": [1920, 1080], "fps": 30},
        "settle_ms": 250,
    }

    step = RecipeStep.from_dict(raw)

    assert step.step_id == "01"
    assert step.name == "Top View"
    assert step.golden_image == "golden_top.png"
    assert step.pose_enabled is False
    assert step.regions == [{"x": 0, "y": 0, "w": 10, "h": 10}]
    assert len(step.tools) == 1
    tool = step.tools[0]
    assert isinstance(tool, Tool)
    assert tool.order == 5
    assert tool.params.values["foo"] == "bar"
    assert pytest.approx(step.thresholds.values["ssim_min"], rel=1e-6) == 0.9
    assert isinstance(step.camera_profile, CameraProfile)
    assert step.camera_profile.resolution == (1920, 1080)
    assert step.camera_profile.fps == 30.0
    assert step.settle_ms == 250


def test_recipe_v2_backward_compatibility_without_steps() -> None:
    raw = {
        "pose_enabled": True,
        "regions": [{"foo": "bar"}],
        "tools": [
            {
                "type": "ssim",
                "name": "ssim",
                "order": 1,
                "params": {},
            }
        ],
        "on_locator_failure": "fail",
        "export_artifacts": True,
    }

    recipe = RecipeV2.from_dict(raw)

    assert recipe.pose_enabled is True
    assert recipe.regions == [{"foo": "bar"}]
    assert len(recipe.tools) == 1
    assert recipe.steps == []
    assert recipe.aggregation == "AND"
    assert recipe.aggregation_weights == {}
    assert recipe.export_artifacts is True


def test_recipe_v2_serializes_steps_and_weights() -> None:
    step = RecipeStep(
        step_id="01",
        name="Top",
        golden_image="golden.png",
        pose_enabled=True,
        regions=[],
        tools=[
            Tool(
                type="ssim",
                name="ssim",
                order=0,
                params=ToolParams({}),
                thresholds=ToolThresholds({}),
            )
        ],
    )

    recipe = RecipeV2(
        pose_enabled=True,
        regions=[],
        tools=[],
        steps=[step],
        aggregation="WEIGHTED",
        aggregation_weights={"01": 2.5},
    )

    data = recipe.to_dict()
    assert data["aggregation"] == "WEIGHTED"
    assert data["steps"][0]["id"] == "01"
    assert pytest.approx(data["aggregation_weights"]["01"], rel=1e-6) == 2.5


@pytest.mark.parametrize(
    "mode,statuses,expected",
    [
        ("AND", ["ok", "warn"], "warn"),
        ("AND", ["ok", "nok"], "nok"),
        ("OR", ["nok", "warn"], "warn"),
        ("OR", ["nok", "ok"], "ok"),
    ],
)
def test_aggregate_step_verdicts_boolean_modes(mode: str, statuses: list[str], expected: str) -> None:
    steps = [
        StepVerdict(step_id=f"{idx}", name=f"step-{idx}", status=status, metrics={})
        for idx, status in enumerate(statuses)
    ]

    result = aggregate_step_verdicts(steps, mode=mode)
    assert result.status == expected
    assert result.mode == mode


def test_aggregate_step_verdicts_weighted_uses_weights() -> None:
    steps = [
        StepVerdict(step_id="A", name="A", status="ok", metrics={}, weight=1.0),
        StepVerdict(step_id="B", name="B", status="nok", metrics={}, weight=3.0),
    ]

    result = aggregate_step_verdicts(steps, mode="WEIGHTED")
    assert result.mode == "WEIGHTED"
    assert result.status == "warn"
    assert 0.0 <= (result.score or 0.0) <= 1.0


def test_load_multi_view_runtime_loads_assets(tmp_path) -> None:
    recipe_dir = tmp_path / "recipes" / "demo"
    step_dir = recipe_dir / "steps" / "step-1"
    step_dir.mkdir(parents=True)

    multi_view_cfg = {
        "aggregation": "OR",
        "steps": [
            {
                "id": "step-1",
                "name": "First",
                "pose_enabled": True,
                "settle_ms": 50,
                "camera_profile": {"resolution": [8, 8], "fps": 10},
            }
        ],
    }
    (recipe_dir / "multi_view.json").write_text(json.dumps(multi_view_cfg), encoding="utf-8")

    golden = np.full((8, 8), 128, dtype=np.uint8)
    iio.imwrite(step_dir / "golden.png", golden)
    (step_dir / "regions.json").write_text(json.dumps([{"x": 0, "y": 0, "w": 8, "h": 8}]), encoding="utf-8")
    (step_dir / "limits.json").write_text(json.dumps({"ssim_min": 0.9}), encoding="utf-8")

    runtime = load_multi_view_runtime("demo", RecipeV2(), base_dir=tmp_path)

    assert not runtime.is_empty()
    assert runtime.aggregation_mode == "OR"
    assert runtime.fail_fast is False
    step = runtime.steps[0]
    assert step.step_id == "step-1"
    assert step.settle_ms == 50
    assert isinstance(step.golden, np.ndarray)
    assert step.golden.shape == (8, 8)
    assert step.limits["ssim_min"] == 0.9


def test_run_multi_view_sequence_produces_ok_status() -> None:
    golden = np.zeros((6, 6), dtype=np.uint8)
    step_config = MultiViewStepConfig(
        step_id="A",
        name="A",
        pose_enabled=True,
        settle_ms=None,
        camera_profile={},
        golden=golden,
        regions=[{"x": 0, "y": 0, "w": 6, "h": 6}],
        limits={"ssim_min": 0.5},
    )
    runtime = MultiViewRuntime(steps=(step_config,), aggregation_mode="AND", weights={}, fail_fast=False)

    result = run_multi_view_sequence(runtime, capture=lambda _cfg: np.zeros((6, 6), dtype=np.uint8))

    assert result.aggregation.status == "ok"
    assert result.steps
    assert result.steps[0].verdict.status == "ok"


def test_run_multi_view_sequence_fail_fast_triggers() -> None:
    golden = np.zeros((4, 4), dtype=np.uint8)
    step_ok = MultiViewStepConfig(
        step_id="A",
        name="A",
        pose_enabled=True,
        settle_ms=None,
        camera_profile={},
        golden=golden,
        regions=[{"x": 0, "y": 0, "w": 4, "h": 4}],
        limits={"ssim_min": 0.5},
    )
    step_nok = MultiViewStepConfig(
        step_id="B",
        name="B",
        pose_enabled=True,
        settle_ms=None,
        camera_profile={},
        golden=golden,
        regions=[{"x": 0, "y": 0, "w": 4, "h": 4}],
        limits={"ssim_min": 0.99},
    )
    runtime = MultiViewRuntime(steps=(step_ok, step_nok), aggregation_mode="AND", weights={}, fail_fast=True)

    frames = [np.zeros((4, 4), dtype=np.uint8), np.full((4, 4), 255, dtype=np.uint8)]

    def _capture(_cfg):
        return frames.pop(0)

    result = run_multi_view_sequence(runtime, capture=_capture)

    assert result.fail_fast_triggered is True
    assert result.aggregation.status == "nok"
    assert len(result.steps) == 2
    assert result.steps[-1].verdict.status == "nok"


def test_run_multi_view_sequence_captures_diagnostics(monkeypatch) -> None:
    golden = np.zeros((3, 3), dtype=np.uint8)
    step = MultiViewStepConfig(
        step_id="A",
        name="A",
        pose_enabled=True,
        settle_ms=None,
        camera_profile={},
        golden=golden,
        regions=[{"x": 0, "y": 0, "w": 3, "h": 3}],
        limits={"ssim_min": 0.5},
    )

    runtime = MultiViewRuntime(
        steps=(step,), aggregation_mode="WEIGHTED", weights={"A": 2.5}, fail_fast=False
    )

    def _fake_analyze(*_args, **_kwargs):
        return {
            "ok": True,
            "metrics": {"foo_score": 0.88},
            "diagnostics": {"debug": "info"},
            "score": 0.91,
        }

    monkeypatch.setattr("app.services.multi_view.analyze", _fake_analyze)

    result = run_multi_view_sequence(runtime, capture=lambda _cfg: np.zeros_like(golden))

    assert result.steps
    step_result = result.steps[0]
    assert step_result.diagnostics == {"debug": "info"}
    assert pytest.approx(step_result.verdict.metrics["foo_score"], rel=1e-6) == 0.88
    assert pytest.approx(step_result.verdict.metrics["score"], rel=1e-6) == 0.91
    assert pytest.approx(step_result.verdict.weight or 0.0, rel=1e-6) == 2.5

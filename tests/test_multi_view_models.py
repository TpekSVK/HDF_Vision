from pathlib import Path
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
from app.services.multi_view import aggregate_step_verdicts, StepVerdict


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

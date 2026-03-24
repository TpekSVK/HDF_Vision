from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

if "app.utils.imaging" not in sys.modules:  # pragma: no cover - test shim
    imaging_stub = types.ModuleType("app.utils.imaging")
    imaging_stub.encode_mask_to_blob = lambda value: value
    imaging_stub.decode_mask_from_blob = lambda value: value
    sys.modules["app.utils.imaging"] = imaging_stub

if "app.services.compare_service" not in sys.modules:  # pragma: no cover - test shim
    compare_stub = types.ModuleType("app.services.compare_service")

    def _analyze_stub(*_args, **_kwargs):
        return {}

    compare_stub.analyze = _analyze_stub
    sys.modules["app.services.compare_service"] = compare_stub

from app.models.schema import RecipeView, ViewCameraProfile
from app.services.recipe_service import RecipeService
from app.ui.view_utils import apply_view_rotation, view_image_rotation, view_uses_global_golden
from app.ui.camera_profile_utils import resolve_view_camera_state


def test_recipe_view_normalizes_camera_profile_and_trigger():
    raw = {
        "id": "view_1",
        "name": "Primary",
        "golden_path": "golden.png",
        "frame_source_view_id": " view_0 ",
        "camera_profile": {
            "width": "1280",
            "height": "720",
            "fps": "60",
            "pixel_format": "y12",
            "exposure_us": "9000",
            "gain_db": "4.5",
        },
        "settle_ms": "45",
        "trigger_mode": "TIMED",
        "trigger_interval_ms": "150",
        "trigger_gap_ms": "20.5",
    }

    view = RecipeView.from_dict(raw)

    assert isinstance(view.camera_profile, ViewCameraProfile)
    assert view.camera_profile.width == 1280
    assert view.camera_profile.pixel_format == "Y12"
    assert view.camera_profile.exposure_us == 9000
    assert view.camera_profile.gain_db == pytest.approx(4.5)
    assert view.settle_ms == 45
    assert view.trigger_mode == "timed"
    assert view.trigger_interval_ms == 150
    assert view.trigger_gap_ms == pytest.approx(20.5)
    assert view.frame_source_view_id == "view_0"

    serialized = view.to_dict()
    assert serialized["trigger_mode"] == "timed"
    assert serialized["trigger_interval_ms"] == 150
    assert serialized["trigger_gap_ms"] == pytest.approx(20.5)
    assert serialized["frame_source_view_id"] == "view_0"
    profile_dict = serialized["camera_profile"]
    assert profile_dict["pixel_format"] == "Y12"
    assert profile_dict["exposure_us"] == 9000


def test_recipe_view_accepts_profile_string():
    view = RecipeView(id="view_1", name="Test", camera_profile="factory_default")
    assert view.camera_profile == "factory_default"
    serialized = view.to_dict()
    assert serialized["camera_profile"] == "factory_default"


def test_recipe_view_external_trigger_submode_normalization():
    timed_view = RecipeView.from_dict(
        {
            "id": "view_timed",
            "name": "Timed",
            "trigger_mode": "timed",
            "external_trigger_mode": "explicit",
            "external_request_input": 4,
        }
    )
    assert timed_view.external_trigger_mode is None
    assert timed_view.external_request_input is None

    legacy_external = RecipeView.from_dict(
        {"id": "view_ext_legacy", "name": "Legacy", "trigger_mode": "external"}
    )
    assert legacy_external.external_trigger_mode == "sequential"
    assert legacy_external.external_request_input is None

    invalid_external = RecipeView.from_dict(
        {
            "id": "view_ext_invalid",
            "name": "Invalid",
            "trigger_mode": "external",
            "external_trigger_mode": "wrong",
            "external_request_input": 99,
        }
    )
    assert invalid_external.external_trigger_mode == "sequential"
    assert invalid_external.external_request_input is None

    explicit_external = RecipeView.from_dict(
        {
            "id": "view_ext_explicit",
            "name": "Explicit",
            "trigger_mode": "external",
            "external_trigger_mode": "explicit",
            "external_request_input": "3",
        }
    )
    assert explicit_external.external_trigger_mode == "explicit"
    assert explicit_external.external_request_input == 3
    serialized = explicit_external.to_dict()
    assert serialized["external_trigger_mode"] == "explicit"
    assert serialized["external_request_input"] == 3


def test_recipe_service_add_and_update_view(tmp_path: Path):
    base_dir = tmp_path / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    service = RecipeService(base_dir=str(base_dir))

    recipe_name = "demo"
    service.create(recipe_name)

    profile = ViewCameraProfile(
        width=1280,
        height=720,
        fps=60,
        pixel_format="Y12",
        exposure_us=8500,
        gain_db=3.25,
    )

    new_view = service.add_view(
        recipe_name,
        view_id="view_custom",
        view_name="Inspection",
        frame_source_view_id=None,
        camera_profile=profile,
        settle_ms=120,
        trigger_mode="timed",
        trigger_interval_ms=250,
        trigger_gap_ms=20.0,
        image_rotation=90,
    )

    assert new_view.id == "view_custom"
    assert new_view.name == "Inspection"
    assert isinstance(new_view.camera_profile, ViewCameraProfile)
    assert new_view.camera_profile.pixel_format == "Y12"
    assert new_view.trigger_mode == "timed"
    assert new_view.trigger_interval_ms == 250
    assert new_view.trigger_gap_ms == pytest.approx(20.0)
    assert new_view.frame_source_view_id is None
    assert new_view.image_rotation == 90

    updated = service.update_view(
        recipe_name,
        new_view.id,
        view_name="Inspection Updated",
        frame_source_view_id="view_source",
        camera_profile=None,
        settle_ms=None,
        trigger_mode="external",
        external_trigger_mode="explicit",
        external_request_input=2,
        trigger_interval_ms=None,
        trigger_gap_ms=24,
        image_rotation=270,
    )

    assert updated.name == "Inspection Updated"
    assert updated.camera_profile is None
    assert updated.trigger_mode == "external"
    assert updated.external_trigger_mode == "explicit"
    assert updated.external_request_input == 2
    assert updated.trigger_interval_ms is None
    assert updated.trigger_gap_ms == pytest.approx(24.0)
    assert updated.frame_source_view_id == "view_source"
    assert updated.image_rotation == 270

    views = {view.id: view for view in service.list_views(recipe_name)}
    assert "view_custom" in views
    assert views["view_custom"].name == "Inspection Updated"
    assert views["view_custom"].frame_source_view_id == "view_source"


def test_view_uses_global_golden_recognizes_per_view_assets():
    default_view = RecipeView(id="view_1", name="Primary", golden_path="golden.png")
    custom_view = RecipeView(id="view_2", name="Secondary", golden_path="golden_view_2.png")
    nested_view = RecipeView(id="view_3", name="Nested", golden_path="images/custom.png")
    empty_path_view = RecipeView(id="view_4", name="Fallback", golden_path="")

    assert view_uses_global_golden(None) is True
    assert view_uses_global_golden(default_view) is True
    assert view_uses_global_golden(custom_view) is False
    assert view_uses_global_golden(nested_view) is False
    assert view_uses_global_golden(empty_path_view) is True


def test_view_branching_serialization_roundtrip():
    raw = {
        "id": "view_0",
        "name": "Router",
        "branch_enabled": True,
        "branch_targets": {"ok": "view_1", "nok": "view_2", "invalid": "skip"},
        "branch_default_view_id": "view_fallback",
    }

    view = RecipeView.from_dict(raw)

    assert view.branch_enabled is True
    assert view.branch_targets == {"ok": "view_1", "nok": "view_2"}
    assert view.branch_default_view_id == "view_fallback"

    serialized = view.to_dict()
    assert serialized["branch_enabled"] is True
    assert serialized["branch_targets"] == {"ok": "view_1", "nok": "view_2"}
    assert serialized["branch_default_view_id"] == "view_fallback"


def test_resolve_camera_state_inherits_base_configuration():
    base = {
        "width": 1280,
        "height": 720,
        "fps": 60,
        "pixel_format": "y8",
        "exposure_us": 8500,
        "gain_db": 3.25,
    }

    state = resolve_view_camera_state(base, None)

    assert state["width"] == 1280
    assert state["height"] == 720
    assert state["fps"] == 60
    assert state["pixel_format"] == "Y8"
    assert state["exposure_us"] == 8500
    assert state["gain_db"] == pytest.approx(3.25)


def test_resolve_camera_state_applies_view_overrides():
    base = {"width": 1280, "height": 720, "fps": 60, "pixel_format": "Y8", "exposure_us": 8000}
    profile = ViewCameraProfile(
        width=1920,
        height=1080,
        fps=90,
        pixel_format="y12",
        exposure_us=9000,
        gain_db=4.5,
    )

    state = resolve_view_camera_state(base, profile)

    assert state["width"] == 1920
    assert state["height"] == 1080
    assert state["fps"] == 90
    assert state["pixel_format"] == "Y12"
    assert state["exposure_us"] == 9000
    assert state["gain_db"] == pytest.approx(4.5)


def test_resolve_camera_state_keeps_missing_overrides_from_base():
    base = {"width": 1280, "height": 720, "fps": 60, "pixel_format": "Y8", "gain_db": 2.0}
    profile = ViewCameraProfile(exposure_us=5000)

    state = resolve_view_camera_state(base, profile)

    assert state["width"] == 1280
    assert state["height"] == 720
    assert state["fps"] == 60
    assert state["pixel_format"] == "Y8"
    assert state["exposure_us"] == 5000
    assert state["gain_db"] == pytest.approx(2.0)


def test_recipe_view_rotation_defaults_for_legacy_data():
    view = RecipeView.from_dict({"id": "view_1", "name": "Legacy"})
    assert view.image_rotation == 0
    assert view.to_dict()["image_rotation"] == 0


def test_recipe_view_rotation_normalization():
    valid = RecipeView.from_dict({"id": "view_1", "name": "Rot", "image_rotation": "90"})
    invalid = RecipeView.from_dict({"id": "view_2", "name": "Bad", "image_rotation": 45})
    assert valid.image_rotation == 90
    assert invalid.image_rotation == 0


def test_apply_view_rotation_clockwise_90():
    import numpy as np

    frame = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
    rotated = apply_view_rotation(frame, 90)
    assert rotated.shape == (3, 2)
    assert rotated.tolist() == [[4, 1], [5, 2], [6, 3]]


def test_view_image_rotation_normalization():
    view = RecipeView(id="view_1", name="Rot", image_rotation=270)
    assert view_image_rotation(view) == 270
    bad = RecipeView(id="view_2", name="Bad", image_rotation=13)
    assert view_image_rotation(bad) == 0

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
from app.ui.view_utils import view_uses_global_golden


def test_recipe_view_normalizes_camera_profile_and_trigger():
    raw = {
        "id": "view_1",
        "name": "Primary",
        "golden_path": "golden.png",
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

    serialized = view.to_dict()
    assert serialized["trigger_mode"] == "timed"
    assert serialized["trigger_interval_ms"] == 150
    profile_dict = serialized["camera_profile"]
    assert profile_dict["pixel_format"] == "Y12"
    assert profile_dict["exposure_us"] == 9000


def test_recipe_view_accepts_profile_string():
    view = RecipeView(id="view_1", name="Test", camera_profile="factory_default")
    assert view.camera_profile == "factory_default"
    serialized = view.to_dict()
    assert serialized["camera_profile"] == "factory_default"


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
        camera_profile=profile,
        settle_ms=120,
        trigger_mode="timed",
        trigger_interval_ms=250,
    )

    assert new_view.id == "view_custom"
    assert new_view.name == "Inspection"
    assert isinstance(new_view.camera_profile, ViewCameraProfile)
    assert new_view.camera_profile.pixel_format == "Y12"
    assert new_view.trigger_mode == "timed"
    assert new_view.trigger_interval_ms == 250

    updated = service.update_view(
        recipe_name,
        new_view.id,
        view_name="Inspection Updated",
        camera_profile=None,
        settle_ms=None,
        trigger_mode="external",
        trigger_interval_ms=None,
    )

    assert updated.name == "Inspection Updated"
    assert updated.camera_profile is None
    assert updated.trigger_mode == "external"
    assert updated.trigger_interval_ms is None

    views = {view.id: view for view in service.list_views(recipe_name)}
    assert "view_custom" in views
    assert views["view_custom"].name == "Inspection Updated"


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

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DummyStorage:
    def __init__(self, images):
        self._images = images

    def load_image(self, path: str):
        normalized = Path(path).as_posix()
        return self._images.get(normalized)


def test_light_transmission_basic_ok_result():
    pytest.importorskip("cv2")
    from app.tools.light_transmission import (
        LightTransmissionCheckParams,
        LightTransmissionCheckTool,
        ToolContext,
    )

    tool = LightTransmissionCheckTool()

    shape = (256, 256)
    I_dark = np.zeros(shape, dtype=np.uint8)
    I_open = np.full(shape, 200, dtype=np.uint8)
    image = (I_dark.astype(np.float32) + 0.45 * (I_open.astype(np.float32) - I_dark.astype(np.float32))).astype(
        np.uint8
    )

    mask = np.zeros(shape, dtype=np.uint8)
    start = 78
    end = start + 100
    mask[start:end, start:end] = 255

    recipe_id = "test"
    dark_path = Path("recipes") / recipe_id / "calib" / "light" / "dark.png"
    open_path = Path("recipes") / recipe_id / "calib" / "light" / "open.png"

    storage = DummyStorage(
        {
            dark_path.as_posix(): I_dark,
            open_path.as_posix(): I_open,
        }
    )

    params = LightTransmissionCheckParams(
        target_T_min=0.40,
        target_T_max=0.50,
        uniformity_max=0.02,
        percentile_bounds=(10, 90),
    )
    context = ToolContext(params=params, storage=storage, recipe_id=recipe_id)

    result = tool.run(image, mask, context)

    assert result.ok is True
    assert abs(result.metrics["T_mean"] - 0.45) < 0.02
    assert result.metrics["T_std"] < 1e-3
    assert "T_heat" in result.debug_images
    heat = result.debug_images["T_heat"]
    assert heat.shape == image.shape
    assert heat.dtype == np.uint8

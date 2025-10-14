import sys
from pathlib import Path
import types

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



class _Cv2Stub:
    MORPH_ELLIPSE = 0
    MORPH_RECT = 1
    MORPH_CROSS = 2
    CV_8UC1 = 0
    THRESH_BINARY = 0
    THRESH_OTSU = 0
    RETR_EXTERNAL = 0
    CHAIN_APPROX_SIMPLE = 0
    INTER_LINEAR = 0
    COLOR_BGR2GRAY = 0
    COLOR_BGRA2GRAY = 0

    def __init__(self) -> None:
        self.cuda = types.SimpleNamespace(
            getCudaEnabledDeviceCount=lambda: 0,
            printShortCudaDeviceInfo=lambda index: None,
            createGaussianFilter=lambda *args, **kwargs: types.SimpleNamespace(
                apply=lambda mat: types.SimpleNamespace(download=lambda: mat)
            ),
            createMorphologyFilter=lambda *args, **kwargs: types.SimpleNamespace(
                apply=lambda mat: mat
            ),
        )

    def __getattr__(self, name: str) -> int:
        return 0

    def cuda_GpuMat(self):
        return types.SimpleNamespace(upload=lambda *args, **kwargs: None, download=lambda: None)

    def getStructuringElement(self, shape, ksize):
        return np.zeros(ksize, dtype=np.uint8)

    def resize(self, image, dsize, interpolation=None):
        return np.zeros((dsize[1], dsize[0]), dtype=getattr(image, "dtype", np.uint8))

    def cvtColor(self, image, code):
        return np.array(image, copy=True)

    def GaussianBlur(self, image, ksize, sigma):
        return np.array(image, copy=True)

    def absdiff(self, src1, src2):
        return np.abs(np.asarray(src1, dtype=np.float32) - np.asarray(src2, dtype=np.float32))


sys.modules.setdefault("cv2", _Cv2Stub())

from app.services.tool_registry import ToolRegistry


def test_list_tool_types_excludes_aliases() -> None:
    """Canonical listing should omit deprecated aliases by default."""

    with_aliases = ToolRegistry.list_tool_types()
    canonical_only = ToolRegistry.list_tool_types(include_aliases=False)

    assert "locator.template_match" in with_aliases
    assert "template_match" in with_aliases
    assert "locator.template_match" in canonical_only
    assert "template_match" not in canonical_only


def test_locator_alias_resolves_to_canonical_definition() -> None:
    """Alias access should still resolve to the canonical tool definition."""

    canonical = ToolRegistry.get_tool_definition("locator.template_match")
    alias = ToolRegistry.get_tool_definition("template_match")

    assert canonical is not None
    assert alias is canonical
    assert canonical.type_id == "locator.template_match"

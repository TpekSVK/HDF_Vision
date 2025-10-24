import importlib
import sys
import types

import numpy as np


def _install_cv2_stub(monkeypatch):
    cv2_stub = types.SimpleNamespace()
    cv2_stub.MORPH_ELLIPSE = 0
    cv2_stub.INTER_LINEAR = 1
    cv2_stub.WARP_INVERSE_MAP = 16
    cv2_stub.TERM_CRITERIA_EPS = 2
    cv2_stub.TERM_CRITERIA_COUNT = 1
    cv2_stub.MOTION_EUCLIDEAN = 1
    cv2_stub.MORPH_CLOSE = 3
    cv2_stub.MORPH_OPEN = 2
    cv2_stub.error = Exception

    cuda_stub = types.SimpleNamespace(
        getCudaEnabledDeviceCount=lambda: 0,
        Stream=type("Stream", (), {"__init__": lambda self, *a, **k: None}),
        registerPageLocked=lambda *a, **k: None,
        unregisterPageLocked=lambda *a, **k: None,
        createBoxFilter=lambda *a, **k: None,
        createGaussianFilter=lambda *a, **k: None,
        createTemplateMatching=lambda *a, **k: None,
        createMorphologyFilter=lambda *a, **k: None,
        createMedianFilter=lambda *a, **k: None,
        createSobelFilter=lambda *a, **k: None,
        createLaplacianFilter=lambda *a, **k: None,
        createCannyEdgeDetector=lambda *a, **k: None,
        createHoughLinesDetector=lambda *a, **k: None,
        createHoughSegmentDetector=lambda *a, **k: None,
        createFilter=lambda *a, **k: None,
        createSumFilter=lambda *a, **k: None,
        createLUT=lambda *a, **k: None,
        createIntegral=lambda *a, **k: None,
        createResize=lambda *a, **k: None,
        createWarpPerspective=lambda *a, **k: None,
        createWarpAffine=lambda *a, **k: None,
        createRemap=lambda *a, **k: None,
        createPyrDown=lambda *a, **k: None,
        createPyrUp=lambda *a, **k: None,
        createCLAHE=lambda *a, **k: None,
        GpuMat=type("GpuMat", (), {"__init__": lambda self, *a, **k: None}),
        setDevice=lambda *a, **k: None,
        resetDevice=lambda *a, **k: None,
        getDevice=lambda: 0,
        getDeviceProperties=lambda *a, **k: types.SimpleNamespace(name="stub"),
    )
    cv2_stub.cuda = cuda_stub

    cv2_stub.GaussianBlur = lambda img, *a, **k: img
    cv2_stub.erode = lambda img, *a, **k: img
    cv2_stub.getStructuringElement = lambda shape, ksize: np.ones(ksize, dtype=np.uint8)
    cv2_stub.bitwise_and = lambda src1, src2, mask=None: src1
    cv2_stub.phaseCorrelate = lambda a, b: ((0.0, 0.0), None)
    cv2_stub.warpAffine = lambda src, *a, **k: src
    cv2_stub.pyrDown = lambda src: src

    def _raise_attr_error(*args, **kwargs):
        raise AttributeError

    cv2_stub.findTransformECC = _raise_attr_error
    cv2_stub.cvtColor = lambda img, code: img
    cv2_stub.COLOR_BGR2GRAY = 6
    cv2_stub.COLOR_RGB2GRAY = 7
    cv2_stub.COLOR_BGRA2GRAY = 8
    cv2_stub.COLOR_RGBA2GRAY = 9
    cv2_stub.resize = lambda img, *a, **k: img
    cv2_stub.split = lambda img: img
    cv2_stub.merge = lambda channels: channels
    cv2_stub.addWeighted = lambda src1, *a, **k: src1
    cv2_stub.absdiff = lambda src1, src2: np.zeros_like(src1)
    cv2_stub.threshold = lambda src, *a, **k: (0.0, np.zeros_like(src))
    cv2_stub.adaptiveThreshold = lambda src, *a, **k: np.zeros_like(src)
    cv2_stub.equalizeHist = lambda src: src
    cv2_stub.createCLAHE = lambda *a, **k: types.SimpleNamespace(apply=lambda img: img)
    cv2_stub.Laplacian = lambda src, *a, **k: np.zeros_like(src)
    cv2_stub.Sobel = lambda src, *a, **k: np.zeros_like(src)
    cv2_stub.Canny = lambda image, *a, **k: np.zeros_like(image)
    cv2_stub.dilate = lambda src, *a, **k: src
    cv2_stub.morphologyEx = lambda src, *a, **k: src

    monkeypatch.setitem(sys.modules, "cv2", cv2_stub)

    imaging_stub = types.SimpleNamespace(
        decode_mask_from_blob=lambda obj: None,
        encode_mask_to_blob=lambda arr: {},
        match_template_u8=lambda *a, **k: (0, 0, 1.0, 0),
    )
    monkeypatch.setitem(sys.modules, "app.utils.imaging", imaging_stub)


def test_publish_recipe_preserves_multi_view_tools(tmp_path, monkeypatch):
    for module in [
        "app.services.recipe_service",
        "app.services.tool_service",
        "app.services.compare_service",
        "app.models.schema",
    ]:
        sys.modules.pop(module, None)

    _install_cv2_stub(monkeypatch)

    recipe_service_module = importlib.import_module("app.services.recipe_service")
    schema_module = importlib.import_module("app.models.schema")

    RecipeService = recipe_service_module.RecipeService
    RecipeView = schema_module.RecipeView
    Tool = schema_module.Tool

    service = RecipeService(base_dir=str(tmp_path))
    service.create("demo")

    service.add_tool(
        "demo",
        Tool(type="locator.default", name="loc-default", order=0, view_id="default"),
    )
    service.add_tool(
        "demo",
        Tool(type="analyzer.default", name="ana-default", order=1, view_id="default"),
    )
    service.save_tools("demo", service.get_draft_tools("demo"))

    new_view = RecipeView(id="view_2", name="View 2", golden_path="golden_view2.png")
    service.add_view("demo", new_view, duplicate_from="default")

    tools = service.get_draft_tools("demo")
    for tool in tools:
        if tool.view_id == "view_2":
            tool.name = f"{tool.name}-v2"
    service.save_tools("demo", tools)

    published_tools, _ = service.publish_recipe("demo")

    view2_names = {tool.name for tool in published_tools if tool.view_id == "view_2"}
    assert view2_names == {"loc-default-v2", "ana-default-v2"}

    stored_tools = service.get_published_tools("demo")
    stored_view2 = {tool.name for tool in stored_tools if tool.view_id == "view_2"}
    assert stored_view2 == {"loc-default-v2", "ana-default-v2"}

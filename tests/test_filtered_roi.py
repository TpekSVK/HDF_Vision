import numpy as np
import cv2
import pytest
from app.models.schema import Tool, ToolRoi, ToolParams, ToolThresholds, RecipeV2
from app.ui.filtered_roi import golden_filtered_roi, compose_filtered_roi
from app.services.tool_service import run_pipeline


def tool(kind='mse', **params):
    return Tool(type=kind, name=kind, enabled=True, order=1,
                roi=ToolRoi({'x': 3, 'y': 4, 'w': 16, 'h': 12}),
                params=ToolParams(params), thresholds=ToolThresholds({}))


@pytest.mark.parametrize('kind', ['mse', 'ssd', 'ncc'])
def test_actual_blur_pixels_and_unmodified_outside(kind):
    image = np.random.default_rng(3).integers(0, 255, (32, 40), dtype=np.uint8)
    original = image.copy()
    t = tool(kind, preblur_sigma=1.0)
    preview = golden_filtered_roi(image, t)
    assert preview is not None
    from app.utils.imaging import blur_gaussian_u8
    np.testing.assert_array_equal(preview['image'], blur_gaussian_u8(image[4:16,3:19], 1.0))
    output = compose_filtered_roi(image, preview)
    np.testing.assert_array_equal(output[4:16,3:19], preview['image'])
    output[4:16,3:19] = original[4:16,3:19]
    np.testing.assert_array_equal(output, original)
    np.testing.assert_array_equal(image, original)


def test_mask_and_translation_preserve_excluded_pixels():
    image = np.full((20, 30, 3), 10, np.uint8)
    mask = np.ones((5, 7), bool); mask[2, 2] = False
    preview = dict(rect=(2,3,7,5), image=np.full((5,7),200,np.uint8), mask=mask,
                   to_display=np.array([[1,0,4],[0,1,2]],np.float32))
    out = compose_filtered_roi(image, preview)
    assert np.all(out[5,6] == 200)
    assert np.all(out[7,8] == 10)
    assert np.all(out[:5] == 10)


def test_pipeline_preview_opt_in_preserves_metrics():
    image = np.random.default_rng(1).integers(0,255,(32,40),dtype=np.uint8)
    recipe = RecipeV2(tools=[tool(preblur_sigma=1.0)], logging_enabled=False)
    a = run_pipeline(image,image,recipe)
    b = run_pipeline(image,image,recipe,capture_filtered_roi=True)
    assert a.per_tool[0].filtered_roi is None
    assert b.per_tool[0].filtered_roi is not None
    assert a.status == b.status
    assert a.per_tool[0].metrics['mse'] == b.per_tool[0].metrics['mse']


def test_no_filter_is_explicit_and_exact():
    image = np.arange(32*40,dtype=np.uint8).reshape(32,40)
    preview = golden_filtered_roi(image, tool())
    assert preview['label'] == 'Bez filtrovania'
    np.testing.assert_array_equal(compose_filtered_roi(image,preview),image)


@pytest.mark.parametrize('kind', ['light_presence', 'presence_absence'])
def test_binary_preview_is_actual_threshold(kind):
    image = np.random.default_rng(4).integers(0,255,(32,40),dtype=np.uint8)
    preview = golden_filtered_roi(image, tool(kind, binary_threshold=128, gaussian_blur_kernel=0))
    assert preview is not None
    np.testing.assert_array_equal(preview['image'], (image[4:16,3:19] > 128).astype(np.uint8)*255)


def test_ssim_input_is_explicitly_unfiltered():
    image = np.arange(32*40,dtype=np.uint8).reshape(32,40)
    preview = golden_filtered_roi(image, tool('ssim'))
    assert 'SSIM' in preview['label']
    np.testing.assert_array_equal(compose_filtered_roi(image,preview),image)


def test_edge_gradient_preview_and_pipeline_parity():
    image = np.random.default_rng(4).integers(0,255,(32,40),dtype=np.uint8)
    t = tool('edge_profile_deviation', point_a=[4,8], point_b=[17,8], blur_sigma=1.0)
    preview = golden_filtered_roi(image, t)
    assert preview is not None and 'gradient' in preview['label']
    recipe = RecipeV2(tools=[t], logging_enabled=False)
    result = run_pipeline(image,image,recipe,capture_filtered_roi=True)
    np.testing.assert_array_equal(result.per_tool[0].filtered_roi['image'], preview['image'])


def test_ellipse_and_ignore_mask_only_replace_valid_roi():
    from app.models.schema import ToolMask
    image = np.random.default_rng(8).integers(0,255,(32,40),dtype=np.uint8)
    t = tool(preblur_sigma=1.0)
    t.roi = ToolRoi({'x':3,'y':4,'w':16,'h':12,'shape':'ellipse'})
    mask = np.zeros_like(image); mask[8:10,8:10] = 255
    t.ignore_mask = ToolMask(mask)
    preview = golden_filtered_roi(image,t)
    out = compose_filtered_roi(image,preview)
    valid = np.zeros_like(image,dtype=bool)
    valid[4:16,3:19] = preview['mask']
    assert not valid[4,3] and not valid[8,8]
    np.testing.assert_array_equal(out[~valid],image[~valid])

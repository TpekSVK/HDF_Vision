"""Display-only composition of tool-produced ROI snapshots."""
import cv2
import numpy as np


def compose_filtered_roi(frame, preview):
    output = frame.copy()
    if not preview:
        return output
    x, y, w, h = preview['rect']
    pixels = preview['image']
    mask = preview['mask']
    if pixels.shape[:2] != (h, w) or mask.shape != (h, w):
        return output
    if x < 0 or y < 0 or y+h > frame.shape[0] or x+w > frame.shape[1]:
        return output
    canvas = np.zeros(frame.shape[:2], np.uint8)
    coverage = np.zeros(frame.shape[:2], np.uint8)
    canvas[y:y+h, x:x+w] = pixels
    coverage[y:y+h, x:x+w] = mask.astype(np.uint8)
    transform = preview.get('to_display')
    if transform is not None:
        size = (frame.shape[1], frame.shape[0])
        canvas = cv2.warpAffine(canvas, transform, size, flags=cv2.INTER_LINEAR)
        coverage = cv2.warpAffine(coverage, transform, size, flags=cv2.INTER_NEAREST)
    if output.ndim == 3:
        canvas = np.repeat(canvas[:, :, None], output.shape[2], axis=2)
    output[coverage != 0] = canvas[coverage != 0]
    return output


def golden_filtered_roi(image, tool):
    # Execute the actual supported tool against the golden itself. Never change
    # its parameters or run model-learning/locator side effects for a preview.
    from app.services.tool_registry import ToolRegistry
    from app.services.tools.common import PairTool
    from app.services.tool_service import ToolRunnerContext, _apply_regions_from_params
    if tool.type not in {"ssim", "mse", "ssd", "ncc", "edge_change", "edge_profile_deviation", "presence_absence", "light_presence"}:
        return None
    runner = ToolRegistry.create_tool(tool.type)
    if not isinstance(runner, PairTool) and type(runner).__name__ != "SSIMTool":
        return None
    supported = {'SSIMTool', 'MSETool', 'SSDTool', 'NCCTool', 'EdgeChangeTool',
                 'EdgeProfileDeviationTool', 'PresenceAbsenceCheckTool', 'LightPresenceCheckTool'}
    if type(runner).__name__ not in supported:
        return None
    copied = tool.copy()
    _apply_regions_from_params(copied)
    context = ToolRunnerContext(frame=image, golden_gray=image)
    runner.prepare({'tool': copied, 'runner_context': context, 'capture_filtered_roi': True})
    try:
        runner.run(image, image, copied.params, copied.thresholds, {'roi': copied.roi})
        return getattr(runner, 'filtered_roi', None)
    finally:
        runner.teardown()

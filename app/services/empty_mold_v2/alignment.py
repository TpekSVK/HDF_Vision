"""Consume the recipe Locator transform; never estimate another transform."""
import numpy as np
from app.utils import imaging


def aligned_frame(context):
    if context.frame_is_aligned:
        source = context.frame_aligned_gray
        if source is None:
            source = imaging.to_gray_u8(context.frame_aligned)
        return source
    source = context.frame_gray
    if source is None:
        source = imaging.to_gray_u8(context.frame)
    matrix = context.T_total
    if matrix is not None and not np.allclose(matrix, [[1, 0, 0], [0, 1, 0]], atol=1e-3):
        return imaging.warp_by_affine_u8(source, imaging.invert_affine(np.asarray(matrix, 'float32')))
    return source


def capture_aligned(golden, frame, tools):
    from app.models.schema import RecipeV2
    from app.services.tool_pipeline import run_pipeline
    locators = [tool.copy() for tool in tools if tool.enabled and tool.type.startswith('locator.')]
    result = run_pipeline(golden, frame, RecipeV2(tools=locators, logging_enabled=False, on_locator_failure='fail'))
    if any(report.status != 'ok' for report in result.per_tool) or any(d.get('locator_failure') for d in result.diagnostics):
        raise ValueError('Zarovnanie zlyhalo; snímka nebola prijatá.')
    return aligned_frame(result.context).copy()

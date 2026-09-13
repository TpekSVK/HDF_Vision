"""Prepare learning pixels with the same locator and ROI path as RUN."""
import numpy as np
from app.models.schema import RecipeV2
from app.services.tool_pipeline import run_pipeline
from app.services.tools.common import PairTool


class LearningSamplePreparer(PairTool):
    def sample(self, golden, frame, target, tools):
        if golden is None or frame is None:
            raise ValueError('Chýba referenčná alebo aktuálna snímka.')
        if np.asarray(golden).shape[:2] != np.asarray(frame).shape[:2]:
            raise ValueError('Rozmery snímky nezodpovedajú referencii.')
        rect = target.roi.rect()
        if rect is None:
            raise ValueError('Pred učením vyberte ROI.')
        x, y, w, h = rect
        height, width = np.asarray(golden).shape[:2]
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise ValueError('ROI učenia presahuje rozmery snímky.')
        # RUN places all locators before analyzers, regardless of list order.
        locators = [tool.copy() for tool in tools if tool.enabled and tool.type.startswith('locator.')]
        recipe = RecipeV2(tools=locators, logging_enabled=False, on_locator_failure='fail')
        result = run_pipeline(golden, frame, recipe)
        if any(report.status != 'ok' for report in result.per_tool) or any(
            item.get('locator_failure') for item in result.diagnostics
        ):
            raise ValueError('Zarovnanie zlyhalo. Snímka sa nezaradí do učenia.')
        self.prepare({'tool': target, 'runner_context': result.context})
        try:
            pair = self._prepare_pair(golden, frame, {'roi': target.roi})
            if pair.pixel_count <= 0:
                raise ValueError('ROI nemá žiadne platné pixely pre učenie.')
            return pair.frame_roi.copy()
        finally:
            self.teardown()

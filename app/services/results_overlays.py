"""Historical NOK context from saved geometry only, never the active recipe."""
from collections.abc import Mapping
from types import SimpleNamespace
from app.utils.nok_label import nok_label


def result_overlays(report):
    entries = [dict(item) for item in (report.get('history_overlays') or []) if isinstance(item, Mapping)]
    if str(report.get('status') or '').lower() != 'nok':
        return entries
    kind = report.get('type') if report.get('thresholds') or report.get('diagnostics') else ''
    saved_tool = SimpleNamespace(name=report.get('name'), type=kind,
        params=SimpleNamespace(values={}), thresholds=SimpleNamespace(values=report.get('thresholds') or {}))
    failure = nok_label(SimpleNamespace(tool=saved_tool, metrics=report.get('metrics') or {},
                                       diagnostics=report.get('diagnostics') or {}))
    for item in entries:
        if item.get('error'):
            item.setdefault('label', failure)
    if any(item.get('error') for item in entries):
        return entries
    # Older records may retain full-image defect coordinates only in metrics.
    metrics = report.get('metrics') or {}
    defects = []
    for blob in metrics.get('blobs', []) or []:
        if isinstance(blob, Mapping) and 'image_x' in blob and 'image_y' in blob:
            defects.append(dict(rect=[blob['image_x'], blob['image_y'], blob.get('width', 0), blob.get('height', 0)], error=True, label=failure))
    if defects:
        return entries + defects
    # RUN highlights the failed inspection area when no spatial defect exists
    # (for example, an untrained model or an SSIM threshold failure).
    context = [dict(item) for item in entries if item.get('rect') or item.get('points')]
    if not context and report.get('roi'):
        from app.models.schema import ToolRoi
        roi = ToolRoi.from_obj(report['roi'])
        if roi.shape() == 'polygon' and roi.points():
            context = [dict(points=roi.points(), closed=True)]
        elif roi.shape() == 'ellipse' and roi.rect():
            import math
            x, y, w, h = roi.rect()
            context = [dict(points=[[x+w/2+math.cos(i*math.tau/64)*w/2,
                                     y+h/2+math.sin(i*math.tau/64)*h/2] for i in range(64)], closed=True)]
        elif roi.rect():
            context = [dict(rect=list(roi.rect()))]
    label = failure + '\nKontrolovaná ROI'
    return entries + [dict(item, error=True, context=True, label=label) for item in context]

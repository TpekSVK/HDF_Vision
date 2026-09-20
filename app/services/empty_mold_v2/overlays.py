"""V2 overlays in aligned or original coordinates using existing overlay geometry."""
from app.models.schema import ToolRoi
from app.utils import overlay


def items(tool, metrics, *, affine=None):
    output = overlay.tool_overlay_items(tool, color=(255, 170, 73),
                                         include_ignore_mask=False, affine=affine)
    for cavity in tool.params.values.get('cavities', []):
        region = tool.copy()
        region.roi = ToolRoi(cavity['roi'])
        output.extend(overlay.tool_overlay_items(region, color=(0, 220, 220), label=cavity['name'],
                                                include_ignore_mask=False, affine=affine))
    if not metrics.get('inspection_fault'):
        for blob in metrics.get('blobs', []):
            region = tool.copy()
            region.roi = ToolRoi(dict(x=blob['x'], y=blob['y'], w=blob['width'], h=blob['height']))
            for item in overlay.tool_overlay_items(region, color=(0, 0, 255),
                    label=' + '.join(blob['zones']), include_ignore_mask=False, affine=affine):
                if item.z_index == 20:
                    item.z_index = 50
                    item.fill_alpha = None
                    output.append(item)
    for item in output:
        item.fill_alpha = None
    return output

"""Golden-coordinate zones. Ignore pixels never participate in any zone."""
import hashlib
import json
import cv2
import numpy as np
from app.models.schema import ToolRoi
from app.services.roi_geometry import roi_shape_mask

OUTSIDE = 'OUTSIDE_CAVITIES'


def shape_mask(shape, descriptor):
    roi = ToolRoi.from_obj(descriptor)
    rect = roi.rect()
    if rect is None:
        raise ValueError('Najskôr nakreslite hlavnú ROI / kavitu.')
    x, y, w, h = rect
    height, width = shape
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError('Oblasť presahuje snímku.')
    mask = np.zeros(shape, bool)
    local = roi_shape_mask(roi, rect)
    mask[y:y+h, x:x+w] = True if local is None else local
    return mask


def zones_for(tool, shape, *, ignore_border=0):
    main = shape_mask(shape, tool.roi)
    ignore = tool.ignore_mask.value
    if ignore is not None:
        ignore = np.asarray(ignore)
        if ignore.shape == shape:
            main &= ignore == 0
        elif ignore.shape == (tool.roi.rect()[3], tool.roi.rect()[2]):
            x, y, w, h = tool.roi.rect()
            main[y:y+h, x:x+w] &= ignore == 0
        else:
            raise ValueError('Ignore maska má nesprávne rozmery.')
    if ignore_border:
        r = int(ignore_border)
        main = cv2.erode(main.astype('uint8'), np.ones((2*r+1, 2*r+1), 'uint8'),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    if not main.any():
        raise ValueError('ROI nemá platné pixely.')
    zones, occupied = {}, np.zeros(shape, bool)
    for cavity in tool.params.values.get('cavities', []):
        name = str(cavity['name'])
        if not name or name == OUTSIDE or name in zones:
            raise ValueError('Kavity musia mať jedinečné názvy.')
        mask = shape_mask(shape, cavity['roi']) & main
        if not mask.any() or (mask & occupied).any():
            raise ValueError('Kavita je prázdna alebo sa prekrýva s inou kavitou.')
        zones[name] = mask
        occupied |= mask
    zones[OUTSIDE] = main & ~occupied
    return zones


def signature(tool, shape):
    zones = zones_for(tool, shape)
    digest = hashlib.sha256(json.dumps(list(zones), sort_keys=True).encode())
    digest.update(str(shape).encode())
    for mask in zones.values():
        digest.update(np.packbits(mask).tobytes())
    return digest.hexdigest()


def annotation_masks(polygons, zones):
    shape = next(iter(zones.values())).shape
    valid = np.logical_or.reduce(list(zones.values()))
    result = []
    for points in polygons:
        array = np.asarray(points, np.float64)
        if array.ndim != 2 or array.shape[1] != 2 or len(array) < 3 or not np.isfinite(array).all():
            raise ValueError('NOK anotácia musí byť platný polygón.')
        if (array < 0).any() or (array[:, 0] >= shape[1]).any() or (array[:, 1] >= shape[0]).any():
            raise ValueError('NOK polygón presahuje snímku.')
        mask = np.zeros(shape, 'uint8')
        cv2.fillPoly(mask, [np.rint(array).astype('int32')], 1)
        mask = mask.astype(bool) & valid
        if not mask.any():
            raise ValueError('NOK polygón neleží v kontrolovanej oblasti.')
        result.append(mask)
    return result

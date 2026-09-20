"""Per-zone signed anomaly masks merged BEFORE morphology and blob analysis."""
import cv2
import numpy as np
from app.services.empty_mold_v2.model import prepare_pixels

DEFAULTS = dict(sensitivity=60, min_blob_area=20, max_blob_area=0, max_blob_count=0,
                polarity='both', anomaly_multiplier=0., zone_thresholds={},
                mean_anomaly_min=0., max_anomaly_min=0., morph_open=0, morph_close=0,
                erode=0, dilate=0, ignore_border=0, normalize=False,
                variability_floor=3., variability_cap=40.)


def settings(tool):
    return {**DEFAULTS, **tool.params.values, **tool.thresholds.values}


def sensitivity_threshold(value):
    value = float(value)
    if not 1 <= value <= 100:
        raise ValueError('Citlivosť musí byť 1–100.')
    return 8. - 6.5 * (value - 1.) / 99.


def evaluate(frame, model, zones, parameters):
    p = {**DEFAULTS, **parameters}
    for key in ('min_blob_area', 'max_blob_area', 'max_blob_count', 'anomaly_multiplier',
                'mean_anomaly_min', 'max_anomaly_min', 'morph_open', 'morph_close', 'erode', 'dilate'):
        if not np.isfinite(float(p[key])) or float(p[key]) < 0:
            raise ValueError(f'Neplatný parameter: {key}')
    if p['polarity'] not in ('bright', 'dark', 'both'):
        raise ValueError('Neplatná polarita.')
    valid = np.logical_or.reduce(list(zones.values()))
    image = prepare_pixels(frame, valid, model.metadata['normalize'])
    if image.shape != model.center.shape or not valid.any():
        raise ValueError('Neplatná geometria modelu.')
    signed = (image - model.center) / model.variability
    score = np.abs(signed)
    anomaly = np.zeros(valid.shape, 'uint8')
    threshold = float(p['anomaly_multiplier']) or sensitivity_threshold(p['sensitivity'])
    for name, mask in zones.items():
        limit = float(p['zone_thresholds'].get(name, threshold))
        if not np.isfinite(limit) or limit <= 0:
            raise ValueError('Neplatný prah zóny.')
        selected = score > limit
        if p['polarity'] == 'bright':
            selected &= signed > 0
        elif p['polarity'] == 'dark':
            selected &= signed < 0
        anomaly[mask & selected] = 1
    score[~valid] = 0
    for key, operation in [('morph_open', cv2.MORPH_OPEN), ('morph_close', cv2.MORPH_CLOSE),
                            ('erode', cv2.MORPH_ERODE), ('dilate', cv2.MORPH_DILATE)]:
        radius = int(p[key])
        if radius > 32:
            raise ValueError('Polomer morfológie je príliš veľký.')
        if radius:
            anomaly = cv2.morphologyEx(anomaly, operation, np.ones((2*radius+1, 2*radius+1), 'uint8'))
            anomaly[~valid] = 0
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(anomaly, 8)
    blobs, detection = [], np.zeros(valid.shape, bool)
    for label in range(1, count):
        x, y, w, h, area = map(int, stats[label])
        if area < p['min_blob_area'] or (p['max_blob_area'] and area > p['max_blob_area']):
            continue
        pixels = labels[y:y+h, x:x+w] == label
        values = score[y:y+h, x:x+w][pixels]
        mean, maximum = float(values.mean()), float(values.max())
        if mean < p['mean_anomaly_min'] or maximum < p['max_anomaly_min']:
            continue
        names = [name for name, mask in zones.items() if (mask[y:y+h, x:x+w] & pixels).any()]
        contours, _ = cv2.findContours(pixels.astype('uint8'), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perimeter = sum(cv2.arcLength(c, True) for c in contours)
        blobs.append(dict(area=area, x=x, y=y, image_x=x, image_y=y, width=w, height=h,
                          centroid=centroids[label].tolist(), zones=names,
                          mean_anomaly=mean, max_anomaly=maximum, aspect_ratio=w/h,
                          fill_ratio=area/(w*h), perimeter=perimeter,
                          circularity=4*np.pi*area/perimeter**2 if perimeter else 0.))
        detection[y:y+h, x:x+w] |= pixels
    blobs.sort(key=lambda b: b['area'], reverse=True)
    nok = len(blobs) > int(p['max_blob_count'])
    zone_results = {name: {'status': 'nok' if nok and any(name in b['zones'] for b in blobs) else 'ok',
                           'blob_count': sum(name in b['zones'] for b in blobs)} for name in zones}
    return dict(status='nok' if nok else 'ok', blobs=blobs, blob_count=len(blobs),
                largest_blob_area=blobs[0]['area'] if blobs else 0, zones=zone_results,
                anomaly=score, binary=anomaly, detection=detection)

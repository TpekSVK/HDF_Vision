"""Read-only polygon-aware validation and deterministic recommendation search."""
import logging
from app.services.empty_mold_v2.evaluator import evaluate
from app.services.empty_mold_v2.regions import annotation_masks

log = logging.getLogger(__name__)


def validate(samples, load_frame, model, zones, parameters):
    summary = dict(ok_correct=0, nok_correct=0, false_nok=0, false_ok=0,
                   missed_polygons=0, false_positive_blobs=0, samples=[])
    for sample in samples:
        result = evaluate(load_frame(sample), model, zones, parameters)
        expected_nok = sample['state'].endswith('_nok')
        truth = annotation_masks(sample['annotations'], zones) if expected_nok else []
        if expected_nok and not truth:
            raise ValueError('Potvrdená NOK vzorka nemá anotáciu.')
        # Minimum 10% coverage (and at least one pixel) per annotated defect.
        hits = [int((result['detection'] & mask).sum()) >= max(1, int(mask.sum() * .1)) for mask in truth]
        missed = sum(not hit for hit in hits)
        false_blobs = 0
        if truth:
            import numpy as np
            import cv2
            union = np.logical_or.reduce(truth)
            count, components = cv2.connectedComponents(result['detection'].astype('uint8'), 8)
            false_blobs = sum(not ((components == i) & union).any() for i in range(1, count))
        elif result['blobs']:
            false_blobs = len(result['blobs'])
        if expected_nok:
            key = 'nok_correct' if result['status'] == 'nok' and missed == 0 else 'false_ok'
        else:
            key = 'ok_correct' if result['status'] == 'ok' else 'false_nok'
        summary[key] += 1
        summary['missed_polygons'] += missed
        summary['false_positive_blobs'] += false_blobs
        summary['samples'].append(dict(id=sample['id'], expected=sample['state'], result=result['status'],
                                       classification=key, missed_polygons=missed,
                                       false_positive_blobs=false_blobs, zones=result['zones']))
    return summary


def recommend(samples, load_frame, model, zones, current):
    if not any(s['state'].endswith('_ok') for s in samples) or not any(s['state'].endswith('_nok') for s in samples):
        raise ValueError('Odporúčania potrebujú potvrdené OK aj NOK vzorky s anotáciami.')
    # Search is offline. Keep images on disk; do not allocate N full float frames.
    candidates = []
    areas = sorted({max(1, round(float(current['min_blob_area']) * factor)) for factor in (.5, 1., 1.5)})
    sensitivities = sorted({20, 40, 60, 80, 95, int(current['sensitivity'])})
    for sensitivity in sensitivities:
        for area in areas:
            for close in (0, 1, 2):
                params = {**current, 'sensitivity': sensitivity, 'anomaly_multiplier': 0.,
                          'min_blob_area': area, 'morph_close': close}
                report = validate(samples, load_frame, model, zones, params)
                objective = (report['false_ok'], report['missed_polygons'], report['false_nok'],
                             report['false_positive_blobs'])
                candidates.append((objective, params, report))
    best_objective = min(c[0] for c in candidates)
    tied = [c for c in candidates if c[0] == best_objective]
    # Prefer an interior point of the equally successful sensitivity interval.
    middle = (min(c[1]['sensitivity'] for c in tied) + max(c[1]['sensitivity'] for c in tied)) / 2
    selected = min(tied, key=lambda c: (abs(c[1]['sensitivity'] - middle),
                                       abs(c[1]['min_blob_area'] - current['min_blob_area']), c[1]['morph_close']))
    log.info('Empty Mold V2 recommendation: objective=%s settings=%s', selected[0], selected[1])
    return {'current': dict(current), 'recommended': selected[1], 'summary': selected[2],
            'trials': len(candidates), 'objective': list(selected[0])}

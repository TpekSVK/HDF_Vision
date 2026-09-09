"""Short explanations from measured values; never changes inspection decisions."""
import math
import unicodedata


def ascii_label(text):
    return unicodedata.normalize('NFKD', str(text).replace('·', '-')).encode('ascii', 'ignore').decode()


def nok_label(report):
    tool = getattr(report, 'tool', None)
    metrics = dict(getattr(report, 'metrics', {}) or {})
    diagnostics = dict(getattr(report, 'diagnostics', {}) or {})
    limits = dict(diagnostics)
    limits.update(dict(getattr(getattr(tool, 'params', None), 'values', {}) or {}))
    limits.update(dict(getattr(getattr(tool, 'thresholds', None), 'values', {}) or {}))
    kind = str(getattr(tool, 'type', '')).lower()
    if kind == 'template_match':
        kind = 'locator.template_match'
    name = ascii_label(getattr(tool, 'name', None) or kind)
    # metric, threshold, failure operator, default used by evaluator, unit
    rules = {
        'ssim': [('ssim', 'ssim_min', '<', .92, '%')],
        'ncc': [('ncc', 'ncc_min', '<', .9, '%')],
        'mse': [('mse', 'mse_max', '>', 25., '')],
        'ssd': [('ssd', 'ssd_max', '>', 1e7, '')],
        'edge_change': [('edge_ratio', 'edge_ratio_max', '>', .05, '%')],
        'edge_profile_deviation': [('max_deviation', 'max_deviation_max', '>', .1, 'px'),
                                   ('coverage', 'coverage_min', '<', .6, '%')],
        'absdiff': [('blob_count', 'max_blob_count', '>', 10, ''),
                    ('total_area', 'max_total_area', '>', 2000, 'px')],
        'locator.template_match': [('corr', 'threshold_corr', '<', .55, '%'),
                                    ('dx', 'max_shift_x', '>', 200, 'px'),
                                    ('dy', 'max_shift_y', '>', 200, 'px')],
    }
    checks = []
    active_rules = list(rules.get(kind, []))
    for flag, metric, threshold, unit in [
        ('fail_area_px', 'anomaly_area', 'total_area_threshold', 'px'),
        ('fail_area_percent', 'anomaly_area_percent', 'max_anomaly_area_percent', 'percent'),
        ('fail_largest_blob', 'largest_blob_area', 'max_largest_blob_area', 'px'),
        ('fail_blob_count', 'blob_count', 'max_blob_count', ''),
    ]:
        if metrics.get(flag) and threshold in limits:
            active_rules.append((metric, threshold, '>', None, unit))
    for metric, threshold, operator, default, unit in active_rules:
        try:
            value, limit = float(metrics[metric]), float(limits.get(threshold, default))
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(value) or not math.isfinite(limit):
            continue
        if metric in {'dx', 'dy'}:
            value = abs(value)
        if not (value < limit if operator == '<' else value > limit):
            continue
        if unit == '%':
            value, limit = value * 100, limit * 100
        suffix = '%' if unit in {'%', 'percent'} else (' ' + unit if unit else '')
        precision = 4 if f'{value:.4g}' != f'{limit:.4g}' else 10
        comparison = f'{value:.{precision}g}{suffix} {operator} {limit:.{precision}g}{suffix}'
        if len(active_rules) > 1:
            comparison = metric + ' ' + comparison
        checks.append(comparison)
    if checks:
        return 'NOK ' + '; '.join(checks) + ' ' + name
    failure = diagnostics.get('alignment_failure')
    reasons = {'reference_edge_not_found': 'Referencna hrana nenajdena',
               'shift_x_out_of_range': 'Posun X mimo rozsahu',
               'shift_y_out_of_range': 'Posun Y mimo rozsahu'}
    reason = reasons.get(failure)
    if not reason and diagnostics.get('alignment_mode') == 'guided_edge':
        reference = diagnostics.get('reference_edge', {})
        if isinstance(reference, dict) and reference.get('found') is False:
            reason = 'Referencna hrana nenajdena'
    if not reason:
        reason = {'model_not_ready': 'Model nie je pripraveny',
                  'no_valid_pixels': 'Ziadne platne pixely v ROI'}.get(metrics.get('decision_reason'))
    if not reason and metrics.get('found') is False:
        reason = 'Objekt nenajdeny'
    if not reason and metrics.get('coverage') == 0:
        reason = 'Hrana nenajdena'
    return f'NOK {reason or "Podmienka kontroly nesplnena"} - {name}'

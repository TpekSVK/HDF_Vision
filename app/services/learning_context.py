"""Deterministic learning provenance, independent of Qt and capture hardware.

Golden pixels are hashed once per invocation; no process-global camera/model state.
Decision thresholds and labels do not change the preparation of training pixels.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.models.learning_contract import SAMPLE_PREPARATION_VERSION

STATISTICAL_TYPES = frozenset({'presence.absence_v2', 'mold.protection_v1'})
MANIFEST_NAME = 'sample_context.json'


def image_digest(image):
    array = np.ascontiguousarray(image)
    return {'shape': list(array.shape), 'dtype': str(array.dtype),
            'sha256': hashlib.sha256(array.tobytes()).hexdigest()}


def _geometry(tool):
    mask = tool.ignore_mask.value
    return {'roi': tool.roi.to_dict(),
            'mask': None if mask is None else image_digest(np.asarray(mask) > 0)}


def learning_signature(golden, view, target, tools, *, golden_digest=None):
    if golden is None or view is None:
        raise ValueError('Chýba golden alebo konfigurácia pohľadu pre učenie.')
    capture_keys = ('camera_profile', 'image_rotation', 'pico_profile',
                    'flash_delay_ms', 'flash_pulse_ms', 'trigger_gap_ms',
                    'frame_source_view_id')
    capture = {key: getattr(view, key, None) for key in capture_keys}
    profile = capture["camera_profile"]
    if hasattr(profile, "to_dict"):
        capture["camera_profile"] = profile.to_dict()
    locators = [tool for tool in sorted(tools, key=lambda item: item.order)
                if tool.enabled and tool.type.startswith('locator.')]
    payload = {
        'context_version': 1, 'preparation_version': SAMPLE_PREPARATION_VERSION,
        'golden': golden_digest if golden_digest is not None else image_digest(golden),
        'capture': capture,
        'target': {'type': target.type, **_geometry(target)},
        'locators': [{'type': tool.type, **_geometry(tool),
                      'params': tool.params.values, 'thresholds': tool.thresholds.values}
                     for tool in locators],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def pipeline_learning_signatures(golden, recipe):
    targets = [tool for tool in recipe.tools if tool.enabled and tool.type in STATISTICAL_TYPES]
    if not targets:
        return {}
    digest = image_digest(golden)
    result = {}
    for tool in targets:
        view = next((view for view in recipe.views if view.id == tool.view_id), None)
        result[id(tool)] = learning_signature(golden, view, tool, recipe.tools, golden_digest=digest)
    return result


def samples_match(base_dir, signature):
    try:
        manifest = json.loads((Path(base_dir) / MANIFEST_NAME).read_text(encoding='utf-8'))
        return (isinstance(manifest, dict)
                and manifest.get('sample_preparation_version') == SAMPLE_PREPARATION_VERSION
                and manifest.get('learning_signature') == signature)
    except (OSError, ValueError):
        return False


def bind_samples(base_dir, signature):
    """Refuse mixing old/missing provenance; persist before writing new samples."""
    base = Path(base_dir)
    has_samples = any(any((base / label).glob('*.png')) for label in ('ok', 'nok'))
    if has_samples and not samples_match(base, signature):
        raise ValueError('Vzorky patria inému nastaveniu. Resetujte učenie a zozbierajte nové vzorky.')
    base.mkdir(parents=True, exist_ok=True)
    path = base / MANIFEST_NAME
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'sample_preparation_version': SAMPLE_PREPARATION_VERSION,
                                     'learning_signature': signature}, indent=2), encoding='utf-8')
    temporary.replace(path)

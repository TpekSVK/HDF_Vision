"""Robust median / 1.4826 MAD model, built offline in bounded row tiles."""
from dataclasses import dataclass
import logging
import numpy as np
from app.utils.imaging import to_gray_u8

log = logging.getLogger(__name__)


@dataclass
class VariationModel:
    center: np.ndarray
    variability: np.ndarray
    metadata: dict


def prepare_pixels(frame, valid, normalize=False):
    image = to_gray_u8(np.asarray(frame)).astype(np.float32)
    if image.shape != valid.shape:
        raise ValueError('Rozmery snímky nezodpovedajú modelu.')
    if normalize:
        image -= np.float32(np.median(image[valid]))
    image[~valid] = 0
    return image


def build(frames, zones, *, normalize=False, variability_floor=3., variability_cap=40.):
    if len(frames) < 2:
        raise ValueError('Potrebné sú aspoň 2 potvrdené OK vzorky; odporúčame 30, ideálne 50+.')
    floor, cap = float(variability_floor), float(variability_cap)
    if not np.isfinite([floor, cap]).all() or floor <= 0 or (cap and cap < floor):
        raise ValueError('Neplatný variability floor/cap.')
    valid = np.logical_or.reduce(list(zones.values()))
    shape = valid.shape
    # Input may be an on-disk uint8 memmap: no full-resolution float sample stack.
    offsets = []
    for frame in frames:
        if frame.shape != shape or frame.dtype != np.uint8:
            raise ValueError('Model očakáva rovnako veľké grayscale uint8 snímky.')
        offsets.append(float(np.median(frame[valid])) if normalize else 0.)
    center, variability = np.zeros(shape, 'float32'), np.zeros(shape, 'float32')
    rows = max(1, min(64, 8_000_000 // (len(frames) * shape[1] * 4)))
    for y in range(0, shape[0], rows):
        stack = np.stack([f[y:y+rows] for f in frames]).astype('float32')
        stack -= np.asarray(offsets, 'float32')[:, None, None]
        median = np.median(stack, axis=0)
        np.subtract(stack, median, out=stack)
        np.abs(stack, out=stack)
        mad = np.median(stack, axis=0) * np.float32(1.4826)
        center[y:y+rows] = median
        variability[y:y+rows] = np.clip(mad, floor, cap or np.inf)
    center[~valid] = 0
    variability[~valid] = floor
    stats = {name: {'pixels': int(mask.sum()),
                    'median_variability': float(np.median(variability[mask])) if mask.any() else 0.}
             for name, mask in zones.items()}
    metadata = dict(normalize=bool(normalize), variability_floor=floor, variability_cap=cap,
                    sample_count=len(frames), zones=stats, method='median_1.4826_MAD_v1')
    log.info('Empty Mold V2 model build: %s', metadata)
    return VariationModel(center, variability, metadata)

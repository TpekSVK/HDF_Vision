"""Frames shared between views carry their current orientation explicitly."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class InspectionFrame:
    """Unaligned inspection pixels, rotated clockwise from the camera image.

    Store these only within a capture cycle. Display overlays and locator-aligned
    frames must never be used as input to another inspection view.
    """
    image: np.ndarray
    rotation: int
    camera_id: str | None = None

    def for_rotation(self, target_rotation: int) -> np.ndarray:
        if self.rotation not in (0, 90, 180, 270) or target_rotation not in (0, 90, 180, 270):
            raise ValueError('Neplatná orientácia snímky.')
        turns = ((target_rotation - self.rotation) % 360) // 90
        return np.rot90(self.image, k=-turns).copy()


def reused_view_frame(spec, captured_frames, target_rotation, *, camera_id=None):
    """Resolve only this cycle's capture; missing dependencies are errors."""
    capture = spec.get('injected_capture')
    source_id = spec.get('frame_source_view_id')
    if capture is None and source_id:
        capture = captured_frames.get(source_id)
        if capture is None:
            raise ValueError(f'Zdrojový pohľad {source_id} nemá snímku v aktuálnej kontrole.')
    if capture is None:
        return None
    if not isinstance(capture, InspectionFrame):
        raise TypeError('Prevzatá snímka nemá informáciu o orientácii.')
    if camera_id is not None and capture.camera_id != camera_id:
        raise ValueError("Zdrojová snímka patrí inej kamere.")
    return capture.for_rotation(target_rotation)

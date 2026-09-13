"""Operator-requested electrical verification of a selected camera/Pico pair."""
from app.services.camera_service import CameraService
from app.services.pico_service import PicoService
from app.services.workstation_devices import resolve_serial


def verify_pair(camera_serial, pico_serial, *, camera_factory=CameraService, pico_factory=PicoService):
    camera = camera_factory(device=f'/dev/unavailable-{camera_serial}',
        device_resolver=lambda: resolve_serial(camera_serial, 'camera'))
    pico = pico_factory(port_resolver=lambda: resolve_serial(pico_serial, 'pico'))
    camera.exposure_us = 1000
    try:
        # Reuse the production PIO path; no guessed mode SET or software pulses.
        return pico.capture_trigger(camera, timeout_s=1.0)
    finally:
        try:
            pico.quiesce()
        finally:
            try:
                camera.stop(caller='pairing_verification')
            finally:
                pico.close()

"""Explicit hardware acceptance run; stores images/checkpoints, never compares MASTER.

Run in the Jetson app Docker image, with camera/HID/Pico devices mapped.
"""
import argparse
import hashlib
import json
import logging
import time
import traceback
from pathlib import Path

import cv2
from app.services.camera_service import CameraService
from app.services.pico_service import PicoService

MODES = [(1920, 1080, 60), (1280, 720, 60), (640, 480, 112), (2592, 1944, 30)]
EXPOSURES = [500, 1000, 2000, 5000, 10000, 15000, 16000]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--switches', type=int, default=3)
    args = parser.parse_args()
    if args.samples < 1 or args.switches < 0:
        parser.error('samples must be positive and switches non-negative')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(out / 'run.log'), logging.StreamHandler()])
    state = {'state': 'RUNNING', 'cells': [], 'switches': [], 'saved': 0,
             'samples_per_cell': args.samples, 'errors': [], 'quality': 'USER_REVIEW_NO_MASTER_COMPARISON'}
    def checkpoint():
        temp = out / 'status.tmp'
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(out / 'status.json')
    def save(frame, name):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), frame):
            raise RuntimeError('PNG save failed: ' + str(target))
        return {'file': name, 'shape': list(frame.shape), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    cam = CameraService()
    pico = PicoService(port='/dev/ttyACM0')
    try:
        ok, status = pico._send_command('STATUS')
        if not ok:
            raise RuntimeError(status or pico.last_error)
        (out / 'pico_before.txt').write_text(status)
        state['camera_uid'] = cam.read_unique_id()
        state['camera_firmware'] = cam.read_firmware_version()
        pico.require_pio()
        for command in ['SESSION IDLE', 'MAP ALL OFF', 'SET V1 DELAY 0', 'SET V1 PULSE 150', 'SET V1 CAPTURE 30']:
            ok, response = pico._send_command(command)
            if not ok:
                raise RuntimeError(response)
        pico._session_mode = 'IDLE'
        for width, height, fps in MODES:
            pico.prepare_master(cam)
            cam.apply_resolution(width=width, height=height, fps=fps, pixel_format='Y8')
            cam.set_brightness(0)
            for exposure in EXPOSURES:
                cell = {'resolution': f'{width}x{height}', 'fps': fps, 'exposure_us': exposure, 'images': [], 'state': 'RUNNING'}
                state['cells'].append(cell); checkpoint()
                cam.set_manual_exposure_us(exposure)
                pico.prepare_trigger(cam)
                generation = cam._gst_start_count
                cell['exposure_readback'] = cam._read_cu55_exposure()
                assert cell['exposure_readback'] * 100 == exposure
                for i in range(args.samples):
                    start = time.monotonic()
                    frame = pico.capture_trigger(cam)
                    assert frame.shape == (height, width)
                    assert cam._gst_start_count == generation, 'Stream reopened during stable captures'
                    record = save(frame, f'{width}x{height}/{exposure}us/{i+1:03d}.png')
                    record['elapsed_ms'] = (time.monotonic() - start) * 1000
                    cell['images'].append(record); state['saved'] += 1; checkpoint()
                cell['state'] = 'COMPLETE'; checkpoint()
                print('CELL_COMPLETE', cell['resolution'], exposure, len(cell['images']), flush=True)
            for i in range(args.switches):
                start = time.monotonic()
                pico.prepare_master(cam)
                master = pico.capture_master('V1', cam)
                assert master.shape == (height, width)
                pico.prepare_trigger(cam)
                triggered = pico.capture_trigger(cam)
                assert triggered.shape == (height, width)
                state['switches'].append({'resolution': f'{width}x{height}', 'index': i,
                    'elapsed_ms': (time.monotonic()-start)*1000, 'state': 'COMPLETE'})
                checkpoint()
            print('SWITCHES_COMPLETE', width, height, args.switches, flush=True)
        state['state'] = 'COMPLETE'
    except BaseException as exc:
        state['state'] = 'FAILED'; state['errors'].append(repr(exc))
        (out / 'error.txt').write_text(traceback.format_exc())
        raise
    finally:
        try:
            pico.prepare_master(cam)
            pico.set_session_mode('IDLE')
        except Exception as exc:
            state['errors'].append('cleanup: '+repr(exc))
            state['state'] = 'FAILED'
        finally:
            cam.stop(caller='pio_acceptance_cleanup'); pico.close()
            state['finished'] = time.strftime('%Y-%m-%d %H:%M:%S'); checkpoint()


if __name__ == '__main__':
    main()

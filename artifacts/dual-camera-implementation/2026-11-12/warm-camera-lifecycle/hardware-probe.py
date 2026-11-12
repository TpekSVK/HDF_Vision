import json
import logging
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from app.models.schema import RecipeV2, RecipeView, ViewCameraProfile, ToolRoi
from app.services.camera_service import CameraService
from app.services.camera_registry import CameraRegistry
from app.services.dual_pico_service import DualPicoTransport
from app.services.modbus_service import ModbusService
from app.services.pico_config_service import PicoConfigService
from app.services.recipe_service import RecipeService
from app.services.results_history import query_history
from app.services.storage_service import save_recipe_config, save_golden
from app.services.tool_registry import ToolRegistry
from app.services.view_capture import ViewCapture
from app.services.workcell_runtime import WorkcellRuntime
from app.services.workstation_devices import discover_devices

logging.basicConfig(level=logging.WARNING)
root = Path('/out'); data = root / 'data-431'; data.mkdir(exist_ok=True)
report = {'status': 'starting', 'cycles': [], 'cleanup_errors': [], 'modbus': 'disabled; no physical PLC writes'}
cameras, before = {}, {}
transport = None
runtime = None
recipes = None
modbus = None

def save():
    (root / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + '\n')

def ctl(device, *args):
    return subprocess.check_output(['v4l2-ctl', '-d', device, *args], text=True, stderr=subprocess.STDOUT)

try:
    found, picos = discover_devices()
    paths = {item.serial: item.path for item in found}
    mapping = {'camera_1': '162A5806', 'camera_2': '1D3A5806'}
    transport = DualPicoTransport(port='/dev/ttyACM0')
    transport.usb_serial = picos[0].serial
    for index, (role, serial) in enumerate(mapping.items(), 1):
        camera = CameraService(paths[serial], width=1280, height=720, fps=60)
        fmt = ctl(camera.device, '--get-fmt-video', '--get-parm')
        width, height = re.search(r'Width/Height\s*:\s*(\d+)/(\d+)', fmt).groups()
        fps = re.search(r'Frames per second:\s*([\d.]+)', fmt)[1]
        before[role] = dict(device=camera.device, serial=serial, width=int(width), height=int(height),
                            fps=float(fps), mode=camera._ensure_hid().get_stream_mode(),
                            exposure=camera._read_cu55_exposure())
        cameras[role] = camera
        endpoint = transport.channels[index]
        endpoint.require_pio()
        report[f'status_before_{index}'] = endpoint.status()
    registry = CameraRegistry(cameras, {role: transport.channels[index] for index, role in enumerate(mapping, 1)},
                              serials=mapping, transport=transport)
    recipes = RecipeService(data)
    if 'DUAL_HARDWARE' not in recipes.list(): recipes.create('DUAL_HARDWARE')
    pico_config = PicoConfigService(data / 'pico_config.json')
    modbus = ModbusService(data / 'modbus_config.json')
    views = []
    for index, (role, camera) in enumerate(cameras.items(), 1):
        endpoint = registry.picos[role]
        profile = ViewCameraProfile(width=1280, height=720, fps=60, pixel_format='Y8', exposure_us=1000)
        tool = ToolRegistry.make_default_tool('mse')
        tool.roi = ToolRoi({'x': 400, 'y': 250, 'w': 200, 'h': 150})
        view = RecipeView(id=f'view_{index}', name=f'Kamera {index}', camera_id=role,
            camera_profile=profile, golden_path=f'golden_view_{index}.png', tools=[tool],
            golden_camera={'camera_id': role, 'camera_serial': mapping[role]},
            output_group='group_1', output_mode='AND', and_timeout_ms=5000,
            trigger_mode='external', external_trigger_mode='explicit', external_source='modbus',
            external_request_input=index)
        endpoint.prepare_master(camera)
        image = ViewCapture(camera, endpoint, pico_config, 'master').capture(
            trigger_mode_label='test_golden', master_caller='dual_hardware', view=view)
        assert image.shape == (720, 1280)
        save_golden(image, 'DUAL_HARDWARE', golden_path=view.golden_path, base_dir=data)
        views.append(view)
    config = RecipeV2(views=views, logging_enabled=True, export_artifacts=True)
    save_recipe_config('DUAL_HARDWARE', config, base_dir=data)
    runtime = WorkcellRuntime(registry, pico_config, modbus, data / 'HDF_Vision.db', data_root=data)
    preparation_intervals = []
    report['preparation_intervals'] = preparation_intervals
    report['preparation_total_ms'] = {}
    report['warm_transitions'] = []
    for role, lane in runtime.runtimes.items():
        original = lane.prepare
        def prepare(*args, original=original, role=role):
            item = dict(camera=role, mode=args[1], started=time.monotonic())
            preparation_intervals.append(item)
            try: return original(*args)
            finally: item['finished'] = time.monotonic()
        lane.prepare = prepare
    outcomes = []
    runtime.on_outcome = lambda outcome: outcomes.append(asdict(outcome))
    report['cameras_before'] = before
    save()
    for mode in ('master', 'trigger'):
        print('PREPARING', mode, flush=True)
        prepared_at = time.monotonic()
        runtime.prepare('DUAL_HARDWARE', mode, 'view_1')
        report['preparation_total_ms'][mode] = (time.monotonic()-prepared_at)*1000
        intervals = [item for item in preparation_intervals if item['mode']==mode][:2]
        assert len(intervals)==2
        assert max(item['started'] for item in intervals) < min(item['finished'] for item in intervals), intervals
        save()
        print('READY', mode, flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for cycle in range(10):
                baselines = {role: (camera.gst_start_count(), camera.stream._pipeline) for role,camera in cameras.items()}
                started_transition = time.monotonic()
                if mode == 'trigger':
                    def preview(item):
                        role,camera=item
                        endpoint=registry.picos[role]
                        endpoint.set_live_preview(True,camera)
                        time.sleep(.15)
                        endpoint.set_live_preview(False,camera)
                        assert camera.pio._pio_ready == camera.pio._pio_signature()
                    list(pool.map(preview,cameras.items()))
                runtime.quiesce()
                for endpoint in registry.picos.values(): assert endpoint._session_mode == 'IDLE'
                started_resume = time.monotonic()
                runtime.prepare('DUAL_HARDWARE',mode,'view_1')
                for role,camera in cameras.items():
                    assert camera.gst_start_count()==baselines[role][0], ('unexpected restart',mode,role)
                    assert camera.stream._pipeline is baselines[role][1]
                report['warm_transitions'].append(dict(mode=mode,cycle=cycle,
                    resume_ms=(time.monotonic()-started_resume)*1000,
                    total_transition_ms=(time.monotonic()-started_transition)*1000,
                    gst_starts={role:camera.gst_start_count() for role,camera in cameras.items()}))
                started = time.monotonic()
                jobs, reason = runtime.admit('manual', {'active_view_id': 'view_1'})
                assert len(jobs) == 2, reason
                results = list(pool.map(runtime.execute, jobs))
                for result in results:
                    if 'error' in result: raise result['error']
                    assert len(result['records']) == 1
                    assert result['records'][0]['frame'].shape[:2] == (720, 1280)
                runtime.complete(jobs, results)
                item = dict(mode=mode, cycle=cycle, elapsed_ms=(time.monotonic()-started)*1000,
                    cameras=[result['records'][0]['camera_id'] for result in results],
                    cycle_ids=[result['cycle_id'] for result in results],
                    statuses=[result['status'] for result in results], outcome=outcomes[-1])
                assert item['cycle_ids'][0] == item['cycle_ids'][1]
                report['cycles'].append(item); save()
                print('CYCLE', mode, cycle, round(item['elapsed_ms'],1), item['statuses'], flush=True)
        runtime.quiesce()
    sources = [dict(id=role, camera_id=role, name=role, db_path=data/'HDF_Vision.db') for role in cameras]
    filters = dict(start=0, end=9999999999999, recipe='', view='', ok=None)
    counts = {source['id']: len(query_history([source], filters, limit=200)) for source in sources}
    assert counts == {'camera_1': 20, 'camera_2': 20}, counts
    report['history_counts'] = counts
    report['status'] = 'passed'
except Exception as exc:
    report['status'], report['error'] = 'failed', repr(exc)
    print('ERROR', repr(exc), flush=True)
finally:
    if runtime is not None:
        try: runtime.quiesce()
        except Exception as exc: report['cleanup_errors'].append('runtime: '+str(exc))
    if transport is not None:
        for index, endpoint in transport.channels.items():
            try:
                endpoint.set_session_mode('IDLE')
                report[f'status_after_{index}'] = endpoint.status()
            except Exception as exc: report['cleanup_errors'].append('pico: '+str(exc))
    for role, camera in cameras.items():
        saved = before[role]
        try:
            camera.pio._pio_set_mode(saved['mode'])
            if saved['exposure'] is not None:
                ctl(camera.device, '-c', f'exposure_time_absolute={saved["exposure"]}')
        except Exception as exc: report['cleanup_errors'].append('mode: '+str(exc))
        finally:
            try: camera.stop(caller='dual-production-cleanup')
            except Exception as exc: report['cleanup_errors'].append('stream: '+str(exc))
        try:
            ctl(camera.device, f'--set-fmt-video=width={saved["width"]},height={saved["height"]},pixelformat=GREY', f'--set-parm={saved["fps"]}')
        except Exception as exc: report['cleanup_errors'].append('format: '+str(exc))
    if transport: transport.close()
    if recipes: recipes.db.close()
    if modbus: modbus.close()
    save()
    print('FINAL', report['status'], report.get('error'), report['cleanup_errors'], flush=True)
if report['status'] != 'passed' or report['cleanup_errors']: sys.exit(1)

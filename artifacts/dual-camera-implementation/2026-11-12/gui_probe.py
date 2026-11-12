import json, logging, shutil, sqlite3, subprocess, time, sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from app.services.workstation_devices import StationBinding, WorkstationDevices, discover_devices
from app.ui.workstation_window import WorkstationWindow
from app.ui.golden_wizard.golden_wizard import GoldenWizard
from app.ui.theme import DARK_STYLE

logging.basicConfig(level=logging.WARNING)
out=Path('/out'); data=out/('data-gui-'+str(time.time_ns())); data.mkdir(exist_ok=True)
source=out/'data-431'
if not (data/'recipes').exists(): shutil.copytree(source/'recipes',data/'recipes')
with sqlite3.connect(source/'HDF_Vision.db') as src, sqlite3.connect(data/'HDF_Vision.db') as dst: src.backup(dst)
(data/'config.json').write_text(json.dumps({'last_recipe':'DUAL_HARDWARE'}))
_,picos=discover_devices()
WorkstationDevices(data/'workstation_devices.json').save([
    StationBinding('camera_1','Kamera 1','162A5806',picos[0].serial,1),
    StationBinding('camera_2','Kamera 2','1D3A5806',picos[0].serial,2)])
report={'status':'starting','steps':[],'cleanup_errors':[]}
before=json.loads((out/'report.json').read_text())['cameras_before']
app=QApplication([]); app.setStyleSheet(DARK_STYLE)
def modal(*args,**kwargs): raise RuntimeError('Unexpected modal: '+str(args[1:]))
QMessageBox.question=lambda *args,**kwargs: QMessageBox.Yes
QMessageBox.critical=modal; QMessageBox.warning=modal; QMessageBox.information=lambda *args: None
shell=None; wizard=None
def pump(predicate,limit=35):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        app.processEvents()
        if predicate(): return
        time.sleep(.01)
    raise RuntimeError('GUI wait timeout')
def step(name):
    report['steps'].append(name); print('STEP',name,flush=True)
try:
    shell=WorkstationWindow(data); shell.resize(1366,768); shell.show()
    main=shell.workspaces[0]
    pump(lambda:not main.runtime_worker.busy)
    assert main.mode=='RUN', main.lbl_status.text()
    step('shared application prepared RUN')
    main._on_view_selected_view('view_2')
    assert main.cam.registry.selected=='camera_2', (main.current_recipe_name(),list(main._views_by_id),main._active_view_id)
    main.btn_trigger.click()
    pump(lambda:not main.runtime.processing and len(main._last_trigger_frames)>=2)
    assert main._active_view_id=='view_2'
    shell.grab().save(str(out/'run-dual.png'))
    step('touch Trigger processed both cameras without switching selected view')
    shell._navigate('RESULTS')
    page=main.panel_results
    pump(lambda:not page.jobs)
    for camera in ('camera_1','camera_2'):
        page.camera.setCurrentIndex(page.camera.findData(camera))
        pump(lambda:not page.jobs and bool(page.rows))
        assert all(row['station_id']==camera for row in page.rows)
        pump(lambda:page._has_image and not page.jobs)
        shell.grab().save(str(out/f'results-{camera}.png'))
    step('Results displays real images filtered by each camera')
    shell._navigate('SETUP'); pump(lambda:not main.runtime_worker.busy and main._pending_runtime_action is None)
    assert main.mode=='SETUP'
    main.capture_mode='trigger'; assert main._apply_capture_mode(ensure_runtime_ready=True)
    wizard=GoldenWizard(main.cam, main.recipes, main, modbus=main.modbus,pico=main.pico,
        get_capture_mode=main.get_capture_mode,capture_frame_for_golden=main.capture_frame_for_golden,
        session_settings=main.session_settings)
    wizard._info=lambda message: None
    wizard._err=lambda message: (_ for _ in ()).throw(RuntimeError(message))
    wizard._warn=wizard._err
    wizard.setWindowState(Qt.WindowNoState); wizard.resize(1366,768); wizard.show(); app.processEvents()
    for index in (1,2):
        ident=f'camera_{index}'
        wizard._switch_active_view(f'view_{index}')
        assert not wizard._camera_capture_error,wizard._camera_capture_error
        assert wizard.cam.registry.selected==ident
        wizard.btn_live.click(); app.processEvents()
        assert wizard._live_on and wizard.pico.registry.picos[ident]._live_camera is wizard.cam.registry.cameras[ident]
        wizard.btn_manual_light.click(); app.processEvents()
        assert wizard.pico.registry.picos[ident].manual_light_status() is True
        peer=f'camera_{3-index}'
        assert wizard.pico.registry.picos[peer].manual_light_status() is False
        wizard.btn_manual_light.click()
        wizard._capture_golden()
        assert wizard.current_img.shape==(720,1280)
        wizard.grab().save(str(out/f'golden-{ident}.png'))
        step(f'{ident}: Trigger LIVE, independent light and Golden capture')
    wizard._has_unsaved_changes=lambda:False; wizard._dirty_views={}; wizard.reject(); wizard=None
    step('Golden wizard closed without disconnecting peer camera')
    report['status']='passed'
except Exception as exc:
    report['status']='failed'; report['error']=repr(exc); print('ERROR',repr(exc),flush=True)
finally:
    if wizard is not None:
        try:
            wizard._has_unsaved_changes=lambda:False; wizard._dirty_views={}; wizard.reject()
        except Exception as exc: report['cleanup_errors'].append('wizard: '+str(exc))
    if shell is not None:
        main=shell.workspaces[0]
        try:
            main._request_runtime_stop()
            pump(lambda:not main.runtime_worker.busy and main._pending_runtime_action is None)
            for ident,camera in main.cam.registry.cameras.items():
                saved=before[ident]
                camera.pio._pio_set_mode(saved['mode'])
                if saved['exposure'] is not None:
                    subprocess.check_call(['v4l2-ctl','-d',camera.device,'-c',f'exposure_time_absolute={saved["exposure"]}'])
            shell.close(); pump(lambda:all(w._close_ready for w in shell.workspaces))
            for ident,saved in before.items():
                subprocess.check_call(['v4l2-ctl','-d',saved['device'],
                    f'--set-fmt-video=width={saved["width"]},height={saved["height"]},pixelformat=GREY',f'--set-parm={saved["fps"]}'])
        except Exception as exc:
            report['cleanup_errors'].append(str(exc))
            try: main.cam.registry.close()
            except Exception: pass
    (out/'gui-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print('FINAL',json.dumps(report,ensure_ascii=False),flush=True)
if report['status']!='passed' or report['cleanup_errors']: sys.exit(1)

import json, logging, shutil, time, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
from app.ui.workstation_window import WorkstationWindow
from app.ui.main_window import MainWindow
from app.services.camera_service import CameraService
from app.services.workstation_devices import discover_devices
import serial, sqlite3
logging.basicConfig(level=logging.WARNING)
out=Path('/out'); data=out/'data'; data.mkdir(exist_ok=True)
shutil.copy2("/source/HDF_Vision.db", data/"HDF_Vision.db") # source copied after clean application shutdown, WAL checkpointed
for filename in ('workstation_devices.json','pico_config.json','modbus_config.json'):
 shutil.copy2(Path('/source')/filename,data/filename)
cfg=json.loads((data/'modbus_config.json').read_text()); cfg['enabled']=False; (data/'modbus_config.json').write_text(json.dumps(cfg))
if not (data/'recipes').exists(): shutil.copytree('/source/recipes',data/'recipes')
(data/'config.json').write_text(json.dumps({'last_recipe':'test2','capture_mode':'trigger'}))
app=QApplication([]); app.setQuitOnLastWindowClosed(False)
QMessageBox.question=lambda *a,**kw:QMessageBox.Yes
QMessageBox.information=lambda *a,**kw:None
QMessageBox.warning=lambda *a,**kw:print('WARNING',a[1:],flush=True)
QMessageBox.critical=lambda *a,**kw:print('CRITICAL',a[1:],flush=True)
report={'status':'starting','cases':[],'cleanup_errors':[],'modbus':'disabled','scope':'Sensor MASTER defaults simulated; no OS reboot performed'}
shell=None; originals={}
def save(): (out/'hardware-report.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
def pump(pred,limit=90):
 end=time.monotonic()+limit
 while time.monotonic()<end:
  app.processEvents()
  if pred():return
  time.sleep(.01)
 raise RuntimeError('GUI wait timed out')
def close():
 global shell
 main=shell.workspaces[0]; transport=main.cam.registry.transport
 shell.close(); pump(lambda:main._close_ready)
 for _ in range(30):app.processEvents();time.sleep(.01)
 assert not transport.is_available(), 'old transport reopened after close'
 with serial.Serial('/dev/ttyACM0',115200,timeout=.1,exclusive=True):pass
 shell.deleteLater();app.processEvents();shell=None
try:
 devices,_=discover_devices()
 for dev in devices:
  if dev.serial not in ('162A5806','1D3A5806'):continue
  cam=CameraService(dev.path,width=2592,height=1944,fps=30)
  originals[dev.serial]=int(cam.get_stream_mode())
  cam.start(caller='simulate_boot_default');cam.pio._pio_set_mode(0);time.sleep(.2);cam.stop(caller='simulate_boot_default');cam.close_hid()
 for case in ('master_defaults','transient_prepare_error','persistent_error_then_manual_retry'):
  attempts=[]; saved={}
  def factory(**kwargs):
   endpoint=kwargs['pico'].registry.picos['camera_1']; original=endpoint.prepare_trigger;saved['endpoint']=endpoint;saved['original']=original
   if case!='master_defaults':
    def injected(cam):
     attempts.append(time.monotonic())
     if case=='persistent_error_then_manual_retry' or len(attempts)==1:raise RuntimeError('Injected startup preparation error')
     return original(cam)
    endpoint.prepare_trigger=injected
   return MainWindow(**kwargs)
  shell=WorkstationWindow(data,workspace_factory=factory);shell.resize(1366,768);shell.show()
  main=shell.workspaces[0];pump(lambda:not main.runtime_worker.busy)
  if case=='persistent_error_then_manual_retry':
   assert len(attempts)==3, attempts
   assert main.mode=='SETUP',main.mode
   saved['endpoint'].prepare_trigger=saved['original']
   main._on_capture_mode_ui_changed(main.cmb_capture_mode.findData('trigger'))
   shell._navigate('RUN');pump(lambda:not main.runtime_worker.busy)
  assert main.mode=='RUN',(case,main.mode,main.lbl_status.text())
  assert main.capture_mode=='trigger'
  if case=='transient_prepare_error':assert len(attempts)==2,len(attempts)
  for view in ('view_1','view_2'):
   main._on_view_selected_view(view);main.btn_trigger.click()
   pump(lambda:not main.runtime.processing and view in main._last_trigger_frames)
   assert main._last_trigger_frames[view] is not None
  baseline={key:cam.gst_start_count() for key,cam in main.cam.registry.cameras.items()}
  shell._navigate('SETUP');pump(lambda:not main.runtime_worker.busy and main._pending_runtime_action is None)
  shell._navigate('RUN');pump(lambda:not main.runtime_worker.busy)
  assert main.mode=='RUN'
  assert baseline=={key:cam.gst_start_count() for key,cam in main.cam.registry.cameras.items()}
  close()
  report['cases'].append({'name':case,'status':'passed','wrapped_prepare_calls':len(attempts),'capture_views':['view_1','view_2'],'warm_navigation':'no pipeline restart','pico_released':True});save();print('PASSED',case,flush=True)
 report['status']='passed'
except Exception as exc:
 import traceback;traceback.print_exc();report.update(status='failed',error=repr(exc))
finally:
 if shell:
  try:close()
  except Exception as exc:report['cleanup_errors'].append(str(exc))
 for dev in discover_devices()[0]:
  if dev.serial in originals:
   try:
    cam=CameraService(dev.path);cam.pio._pio_set_mode(originals[dev.serial]);cam.close_hid()
   except Exception as exc:report['cleanup_errors'].append(str(exc))
 save();print('FINAL',report,flush=True)
sys.exit(report['status']!='passed' or bool(report['cleanup_errors']))

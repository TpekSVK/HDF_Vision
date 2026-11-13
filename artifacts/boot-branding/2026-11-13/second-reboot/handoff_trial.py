"""Transient hardware check; do not install an early-display boot configuration."""
import json, subprocess, time
from pathlib import Path
out=Path('/tmp/hdf-third-boot')
report={'status':'starting','steps':[],'persistent_boot_changes':False}
def run(*args, check=True):
 result=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=35)
 report['steps'].append({'command':list(args),'status':result.returncode,'output':result.stdout[-3000:]})
 if check and result.returncode:raise RuntimeError(str(args)+': '+result.stdout)
 return result
try:
 # The application must release the board before its desktop is stopped.
 for _ in range(20):
  busy=subprocess.run(['fuser','/dev/ttyACM0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
  if not busy:break
  time.sleep(.5)
 if busy:raise RuntimeError('Pico is still owned; display test not started')
 run('systemctl','stop','gdm.service')
 run('plymouth','quit',check=False)
 run('systemctl','stop','plymouth-start.service',check=False)
 run('modprobe','-r','nvidia_drm')
 run('modprobe','nvidia_drm')
 run('systemctl','start','plymouth-start.service')
 run('plymouth','show-splash')
 print('HDF Plymouth visible phase: eight seconds',flush=True)
 time.sleep(8)
 # Acknowledge quit before releasing the module. No forced unload.
 run('plymouth','quit','--retain-splash')
 run('systemctl','stop','plymouth-start.service')
 run('modprobe','-r','nvidia_drm')
 report['status']='handoff_completed'
except Exception as exc:
 report['status']='failed';report['error']=repr(exc)
finally:
 run('plymouth','quit',check=False)
 run('systemctl','stop','plymouth-start.service',check=False)
 run('systemctl','start','gdm.service',check=False)
 (out/'handoff-trial.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report),flush=True)

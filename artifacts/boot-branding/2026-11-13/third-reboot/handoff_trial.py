import subprocess,time,json
from pathlib import Path
out=Path('/tmp/hdf-fourth-boot');report={'status':'starting','steps':[],'purpose':'Test the actual GDM ExecStartPre, no manual cleanup between Plymouth and GDM'}
def run(*args,check=True):
 r=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=35)
 report['steps'].append(dict(command=list(args),code=r.returncode,output=r.stdout[-2000:]))
 if check and r.returncode:raise RuntimeError(str(args)+r.stdout)
try:
 for _ in range(20):
  busy=subprocess.run(['fuser','/dev/ttyACM0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
  if not busy:break
  time.sleep(.5)
 if busy:raise RuntimeError('Pico still owned; test not started')
 run('systemctl','stop','gdm.service')
 run('plymouth','quit',check=False)
 run('systemctl','stop','plymouth-start.service',check=False)
 run('modprobe','-r','nvidia_drm')
 run('modprobe','nvidia_drm')
 run('systemctl','start','plymouth-start.service')
 run('plymouth','show-splash')
 time.sleep(3)
 run('systemctl','start','gdm.service')
 report['status']='gdm_started_with_installed_handoff'
except Exception as exc:
 report.update(status='failed',error=repr(exc))
 run('plymouth','quit',check=False)
 run('systemctl','stop','plymouth-start.service',check=False)
 run('modprobe','-r','nvidia_drm',check=False)
 run('systemctl','start','gdm.service',check=False)
finally:
 (out/'handoff-trial.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)

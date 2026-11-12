import json, re, subprocess, sys, time
from pathlib import Path
root=Path('/workspace/tests'); out=Path('/testdeps/regression-isolated'); out.mkdir(exist_ok=True)
files=sorted(root.glob('test_*.py'))
ui=[p for p in files if any(s in p.read_text() for s in ('QApplication','PySide6','test_workstation_window import','test_golden_touch import'))]
unit=[p for p in files if p not in ui]
batches=[('unit',unit)]+[(p.stem,[p]) for p in ui]
results=[]
for name,batch in batches:
 start=time.monotonic()
 try:
  run=subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider',*[str(p) for p in batch]],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900 if name == "test_golden_touch" else 240)
  text=run.stdout.decode(errors='replace'); status=run.returncode
 except subprocess.TimeoutExpired as exc:
  text=(exc.stdout or b'').decode(errors='replace'); status=124
 (out/(name+'.log')).write_text(text)
 summary=next((line for line in reversed(text.splitlines()) if re.search(r'\d+ (passed|failed|error)',line)),'no summary')
 item=dict(batch=name,status=status,elapsed=round(time.monotonic()-start,2),summary=summary)
 results.append(item); (out/'summary.json').write_text(json.dumps(results,indent=2))
 print(json.dumps(item),flush=True)
print('FINAL',len(results),'batches',sum(r['status']!=0 for r in results),'failed batches',flush=True)
sys.exit(any(r['status']!=0 for r in results))

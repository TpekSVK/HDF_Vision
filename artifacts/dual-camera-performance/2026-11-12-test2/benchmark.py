import json,logging,re,subprocess,sys,time,threading,os,hashlib,random,statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import cv2
from app.services.camera_service import CameraService
from app.services.camera_registry import CameraRegistry
from app.services.dual_pico_service import DualPicoTransport
from app.services.modbus_service import ModbusService
from app.services.pico_config_service import PicoConfigService
from app.services.workcell_runtime import WorkcellRuntime
from app.services.workstation_devices import WorkstationDevices,resolve_serial
from app.services.storage_service import load_recipe_config
import app.services.inspection_runtime as single

logging.basicConfig(level=logging.WARNING)
root=Path('/out');data=root/'data';name='test2'
report=dict(status='starting',recipe=name,modbus='disabled; no physical PLC writes',rows=[],cleanup_errors=[],opencv_threads=cv2.getNumThreads(),load_before=os.getloadavg())
cameras={};before={};transport=None;runtime=None;modbus=None
tls=threading.local()
original_pipeline=single.run_pipeline
def pipeline(*args,**kwargs):
    start=time.perf_counter()
    result=original_pipeline(*args,**kwargs)
    tls.pipeline=dict(start=start,end=time.perf_counter(),tool_ms=[float(r.latency_ms) for r in result.per_tool])
    return result
single.run_pipeline=pipeline

def save():
    (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str)+'\n')
def ctl(device,*args):
    return subprocess.check_output(['v4l2-ctl','-d',device,*args],text=True,stderr=subprocess.STDOUT)
def percentile(vals,p):
    vals=sorted(vals);x=(len(vals)-1)*p;lo=int(x);hi=min(lo+1,len(vals)-1)
    return vals[lo]+(vals[hi]-vals[lo])*(x-lo)
def stats(vals):
    return dict(mean=statistics.mean(vals),median=statistics.median(vals),p95=percentile(vals,.95),min=min(vals),max=max(vals))
try:
    bindings=WorkstationDevices(data/'workstation_devices.json').load()
    transport=DualPicoTransport(port=resolve_serial(bindings[0].pico_serial,'pico'));transport.usb_serial=bindings[0].pico_serial
    for binding in bindings:
        camera=CameraService(resolve_serial(binding.camera_serial,'camera'))
        fmt=ctl(camera.device,'--get-fmt-video','--get-parm')
        w,h=re.search(r'Width/Height\s*:\s*(\d+)/(\d+)',fmt).groups()
        fps=float(re.search(r'Frames per second:\s*([\d.]+)',fmt)[1])
        before[binding.id]=dict(device=camera.device,width=int(w),height=int(h),fps=fps,mode=camera._ensure_hid().get_stream_mode(),exposure=camera._read_cu55_exposure())
        cameras[binding.id]=camera
    report['cameras_before']=before
    registry=CameraRegistry(cameras,{b.id:transport.channels[b.pico_channel] for b in bindings},names={b.id:b.name for b in bindings},serials={b.id:b.camera_serial for b in bindings},transport=transport)
    modbus=ModbusService(data/'benchmark_modbus_disabled.json')
    assert not modbus.get_config().enabled
    config=load_recipe_config(name,base_dir=data)
    report['recipe_sha256']=hashlib.sha256((data/'recipes'/name/'recipe.published.json').read_bytes()).hexdigest()
    report['views']=[dict(id=v.id,name=v.name,camera_id=v.camera_id,camera_profile=v.to_dict()['camera_profile'],tools=[dict(type=t.type,name=t.name,roi=t.roi.to_dict()) for t in v.tools if t.enabled]) for v in config.views]
    assert len(config.views)==2 and {v.camera_id for v in config.views}==set(cameras)
    assert all(v.output_mode=='independent' for v in config.views)
    mode='trigger' if before[bindings[0].id]['mode']==1 else 'master'
    report.update(capture_mode=mode,logging_enabled=config.logging_enabled,export_artifacts=config.export_artifacts)
    runtime=WorkcellRuntime(registry,PicoConfigService(data/'pico_config.json'),modbus,data/'benchmark.db',data_root=data)
    for lane in runtime.runtimes.values():
        capture=lane._capture_frame_for_view
        def measured_capture(*args,original=capture,**kwargs):
            start=time.perf_counter();result=original(*args,**kwargs)
            tls.capture=dict(start=start,end=time.perf_counter())
            return result
        lane._capture_frame_for_view=measured_capture
    report['setup_retries'] = []
    for attempt in range(3):
        try:
            runtime.prepare(name,mode,config.views[0].id)
            break
        except RuntimeError as exc:
            if 'Invalid HID command echo' not in str(exc) or attempt==2: raise
            report['setup_retries'].append(str(exc));save();time.sleep(.3)
    report['starts_after_prepare']={ident:cam.gst_start_count() for ident,cam in cameras.items()}
    save()
    conditions={'solo_1':[config.views[0]],'solo_2':[config.views[1]],'simultaneous':config.views}
    def measure(condition,iteration,warmup,pool):
        selected=conditions[condition];jobs=[]
        for view in selected:
            accepted,reason=runtime.admit('manual',{'active_view_id':view.id})
            assert len(accepted)==1,reason
            jobs.extend(accepted)
        common={};gate=threading.Barrier(len(jobs),action=lambda:common.update(start=time.perf_counter()))
        def work(job):
            tls.pipeline=None;tls.capture=None
            gate.wait(timeout=10);start=time.perf_counter()
            result=runtime.execute(job)
            if 'error' in result: raise result['error']
            record=result['records'][0]
            pipeline=dict(tls.pipeline);capture=dict(tls.capture)
            executed=time.perf_counter()
            runtime.complete([job],[result])
            end=time.perf_counter()
            return dict(camera_id=job.camera_id,view_id=record['view_id'],status=record['status'],worker_start=start,
                pipeline=pipeline,capture=capture,pipeline_ms=(pipeline['end']-pipeline['start'])*1000,
                displayed_pipeline_ms=record['cycle_time_ms'],processing_ms=record['processing_time_ms'],capture_ms=record['capture_time_ms'],
                displayed_total_ms=record['total_cycle_time_ms'],worker_ms=(executed-start)*1000,complete_ms=(end-start)*1000,
                evaluation_done=pipeline['end'],completed=end)
        results=[f.result(timeout=60) for f in [pool.submit(work,job) for job in jobs]]
        baseline=common['start']
        item=dict(condition=condition,iteration=iteration,warmup=warmup,views=results,
            both_evaluated_ms=(max(x['evaluation_done'] for x in results)-baseline)*1000,
            both_complete_ms=(max(x['completed'] for x in results)-baseline)*1000,
            start_skew_ms=(max(x['worker_start'] for x in results)-min(x['worker_start'] for x in results))*1000,
            evaluation_overlap_ms=max(0,min(x['pipeline']['end'] for x in results)-max(x['pipeline']['start'] for x in results))*1000)
        report['rows'].append(item);save()
        print('ROUND',iteration,condition,'warmup' if warmup else 'measured','pipeline',[round(x['pipeline_ms'],1) for x in results],'capture',[round(x['capture_ms'],1) for x in results],'both_eval',round(item['both_evaluated_ms'],1),'full',round(item['both_complete_ms'],1),flush=True)
        time.sleep(.05)
    rng=random.Random(1211)
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='benchmark') as pool:
        for i in range(23):
            order=list(conditions);rng.shuffle(order)
            for condition in order:measure(condition,i,i<3,pool)
    measured=[r for r in report['rows'] if not r['warmup']]
    report['summary']={}
    for condition in conditions:
        rows=[r for r in measured if r['condition']==condition]
        entry=dict(n=len(rows),both_evaluated_ms=stats([r['both_evaluated_ms'] for r in rows]),both_complete_ms=stats([r['both_complete_ms'] for r in rows]),views={})
        for view in conditions[condition]:
            vals=[v for r in rows for v in r['views'] if v['view_id']==view.id]
            entry['views'][view.id]={key:stats([v[key] for v in vals]) for key in ('pipeline_ms','displayed_pipeline_ms','processing_ms','capture_ms','displayed_total_ms','complete_ms')}
        report['summary'][condition]=entry
    report['starts_after_measurement']={ident:cam.gst_start_count() for ident,cam in cameras.items()}
    assert report['starts_after_measurement']==report['starts_after_prepare']
    report.update(status='passed',load_after=os.getloadavg())
except Exception as exc:
    report.update(status='failed',error=repr(exc));print('ERROR',repr(exc),flush=True)
finally:
    if runtime:
        try:runtime.quiesce()
        except Exception as exc:report['cleanup_errors'].append(str(exc))
    if transport:
        for index,endpoint in transport.channels.items():
            try:endpoint.set_session_mode('IDLE');report[f'status_after_{index}']=endpoint.status()
            except Exception as exc:report['cleanup_errors'].append(str(exc))
    for role,camera in cameras.items():
        original=before[role]
        try:
            camera.pio._pio_set_mode(original['mode'])
            ctl(camera.device,'-c',f'exposure_time_absolute={original["exposure"]}')
        except Exception as exc:report['cleanup_errors'].append(str(exc))
        finally:
            try:camera.stop(caller='benchmark_cleanup')
            except Exception as exc:report['cleanup_errors'].append(str(exc))
        try:ctl(camera.device,f'--set-fmt-video=width={original["width"]},height={original["height"]},pixelformat=GREY',f'--set-parm={original["fps"]}')
        except Exception as exc:report['cleanup_errors'].append(str(exc))
    if transport:transport.close()
    if modbus:modbus.close()
    save();print('FINAL',report['status'],report.get('error'),report['cleanup_errors'],flush=True)
if report['status']!='passed' or report['cleanup_errors']:sys.exit(1)

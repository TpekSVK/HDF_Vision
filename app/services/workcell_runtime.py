"""A shared recipe with independent camera lanes and grouped PLC decisions."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import uuid
from pathlib import Path

from app.services.inspection_runtime import InspectionRuntime
from app.services.inspection_controller import InspectionController, InspectionState
from app.services.inspection_groups import InspectionGroups
from app.services.dual_camera_rules import validate_view_routing
from app.services.storage_service import load_recipe_config


class _NoOutputs:
    def signal_result(self, *args, **kwargs): pass
    def emit_heartbeat(self, *args, **kwargs): pass
    def close(self): pass


@dataclass
class CameraJob:
    ticket: object
    camera_id: str
    request: object
    options: dict


class WorkcellRuntime:
    def __init__(self, registry, pico_config, modbus, db_path, *, data_root, session_settings=None):
        self.registry, self.modbus = registry, modbus
        self.data_root = Path(data_root)
        self.db_path = Path(db_path)
        self.capture_mode = 'master'
        self.groups = None
        self.last_outcomes = {}
        self.latest_groups = {}
        self.on_outcome = lambda outcome: None
        self.on_idle = lambda: None
        self.on_error = lambda reason: None
        self._admission_lock = threading.RLock()
        self._inflight = set()
        self._frame_sources = {}
        self._recovering = {}
        self._recovery_cancel = threading.Event()
        self.on_recovery = lambda record: None
        self._hardware_fault = ''
        self.runtimes = {ident: InspectionRuntime(camera, registry.picos[ident], pico_config,
            _NoOutputs(), db_path, data_root=self.data_root, camera_id=ident,
            pico_id=getattr(registry.transport, 'usb_serial', None) or 'shared_pico',
            session_settings=session_settings) for ident, camera in registry.cameras.items()}
        for ident, runtime in self.runtimes.items():
            runtime.allowed_camera_id = ident
            runtime.camera_serial = registry.serials.get(ident)
            runtime.camera_name = registry.names[ident]
        self.controllers = {ident: InspectionController(ident) for ident in self.runtimes}

    @property
    def processing(self):
        with self._admission_lock:
            return bool(self._inflight or self._recovering) or (self.groups is not None and self.groups.processing)

    @property
    def busy(self):
        return self.processing or (self.groups is not None and self.groups.busy)

    def _publish(self, outcome, *, recipe_name=None):
        with self._admission_lock:
            self.last_outcomes[outcome.cycle_id] = outcome
            self.latest_groups[outcome.group_id] = outcome
            if len(self.last_outcomes) > 100:
                self.last_outcomes.pop(next(iter(self.last_outcomes)))
        try:
            self.modbus.emit_heartbeat(group_id=outcome.group_id)
            self.modbus.signal_result(outcome.status, group_id=outcome.group_id)
        finally:
            from app.services.db_service import DbService
            database = DbService(self.db_path)
            try:
                database.insert_group_result(recipe_name or self.recipe_name, outcome)
            finally:
                database.close()
            # Persist the final group verdict separately from camera images.
            # A timed-out cycle remains visible even if no second image exists.
            path = self.data_root / 'logs' / 'inspection_groups.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            from dataclasses import asdict
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(asdict(outcome), ensure_ascii=False) + '\n')
            self.on_outcome(outcome)

    def prepare(self, recipe_name, capture_mode, active_view_id):
        self._hardware_fault = ''
        config = load_recipe_config(recipe_name, base_dir=self.data_root)
        validate_view_routing(config.views, complete=True, available_cameras=self.registry.cameras)
        for view in config.views:
            identity = view.golden_camera
            if self.registry.serials and view.camera_id == 'camera_2' and not identity:
                raise ValueError(f'{view.name}: zachyťte a uložte GOLDEN z kamery 2 v Golden Wizard.')
            if identity and (identity['camera_id'] != view.camera_id or
                    (identity.get('camera_serial') and identity['camera_serial'] != self.registry.serials.get(view.camera_id))):
                raise ValueError(f'{view.name}: zmenila sa kamera. Zachyťte a uložte nový GOLDEN a overte učenie.')
        cfg = self.modbus.get_config()
        for group in {view.output_group for view in config.views if view.output_group}:
            cfg.group_config(group)
        if capture_mode is None:
            first = self.registry.cameras[config.views[0].camera_id]
            for attempt in range(3):
                try:
                    capture_mode = 'trigger' if int(first.get_stream_mode()) == 1 else 'master'
                    break
                except (RuntimeError, OSError):
                    close_hid = getattr(first, 'close_hid', None)
                    if close_hid is not None:
                        close_hid()
                    if attempt == 2:
                        raise
                    time.sleep(.25)
        self.capture_mode, self.recipe_name = capture_mode, recipe_name
        if self.groups:
            self.groups.close()
        self.last_outcomes.clear()
        self.latest_groups.clear()
        self.groups = InspectionGroups(config.views, lambda outcome, name=recipe_name:
                                       self._publish(outcome, recipe_name=name),
                                       on_idle=lambda: self.on_idle(), on_error=lambda reason: self.on_error(reason))
        self.config = config
        self._frame_sources.clear()
        self._recovery_cancel.clear()
        def prepare_camera(item):
            ident, runtime = item
            views = [view for view in config.views if view.camera_id == ident]
            if not views:
                return
            selected = next((view for view in views if view.id == active_view_id), views[0])
            # New channel firmware is mandatory even when only one role is
            # configured in the shared-board topology.
            self.registry.picos[ident].require_pio()
            pico = self.registry.picos[ident]
            if capture_mode == 'master':
                for view in views:
                    profile = view.pico_profile or 'V1'
                    value = (view.flash_delay_ms, view.flash_pulse_ms)
                    if hasattr(pico, 'configure_view_flash') and not pico.configure_view_flash(profile, *value):
                        raise RuntimeError(pico.last_error or 'Nastavenie blesku zlyhalo.')
            for view in views:
                if (view.trigger_mode == 'external' and view.external_source == 'pico'
                        and view.external_trigger_mode == 'explicit'):
                    if not self.runtimes[ident].pico_config.is_input_enabled(view.external_request_input):
                        raise ValueError(f'{view.name}: povoľte Pico IN{view.external_request_input} v Pico sprievodcovi.')
                    if not pico.map_input(view.external_request_input, view.pico_profile or 'V1'):
                        raise RuntimeError(pico.last_error or 'Priradenie vstupu Pica zlyhalo.')
            controller = self.controllers[ident]
            if not controller.prepare(lambda rt=runtime, view=selected: rt.prepare(
                    recipe_name, capture_mode, view.id), runtime.quiesce):
                raise RuntimeError(controller.snapshot()['error'])
        try:
            # Each task owns a concrete camera and Pico channel. Wait for both
            # tasks before cleanup, including when one preparation fails.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="camera-prepare") as pool:
                list(pool.map(prepare_camera, self.runtimes.items()))
            if self._hardware_fault:
                raise RuntimeError(self._hardware_fault)
        except Exception:
            self.quiesce()
            raise

    def targets(self, source, options):
        camera_id = options.get('camera_id')
        spec = options.get('spec') or {}
        explicit = spec.get('view_id')
        if explicit:
            return [view for view in self.config.views if view.id == explicit
                    and (camera_id is None or view.camera_id == camera_id)]
        if source == 'manual':
            selected = self.config.get_view(options.get('active_view_id'))
            if selected.output_mode == 'AND':
                return [view for view in self.config.views if view.output_group == selected.output_group]
            return [selected]
        views = [view for view in self.config.views
                 if view.trigger_mode == 'external' and view.external_source == source
                 and (camera_id is None or view.camera_id == camera_id)]
        index = options.get('input_index')
        if type(index) is not int or not 1 <= index <= 8:
            return []
        # Sequential external inputs retain a position per camera/source.
        selected = []
        for ident in self.runtimes:
            explicit_views = [view for view in views if view.camera_id == ident
                              and view.external_trigger_mode == 'explicit'
                              and view.external_request_input == index]
            if explicit_views:
                selected.extend(explicit_views)
                continue
            sequence = [view for view in views if view.camera_id == ident
                        and view.external_trigger_mode == 'sequential']
            if sequence:
                runtime = self.runtimes[ident]
                position = runtime._external_sequence_index.get(source, 0) % len(sequence)
                selected.append(sequence[position])
        return selected

    def admit(self, source, options):
        with self._admission_lock:
            if self._hardware_fault:
                return [], self._hardware_fault
            if self.groups is None:
                return [], 'RUN nie je pripravený'
            views = self.targets(source, options)
            # One accepted request owns all AND views of its camera. It never
            # requires a forbidden second request to that already-held lane.
            expanded = {view.id: view for view in views}
            for view in list(views):
                if view.output_mode == 'AND':
                    for member in self.config.views:
                        if member.camera_id == view.camera_id and member.output_group == view.output_group:
                            expanded[member.id] = member
            views = [view for view in self.config.views if view.id in expanded]
            if not views:
                return [], 'vstup nemá priradený pohľad'
            for ident in {view.camera_id for view in views}:
                if self.controllers[ident].state != InspectionState.READY:
                    return [], f'{ident}: kamera nie je pripravená alebo je obsadená'
            ticket, reason = self.groups.admit([view.id for view in views])
            if ticket is None:
                return [], reason
            jobs = []
            admitted = []
            try:
                for ident in sorted(ticket.cameras):
                    controller = self.controllers[ident]
                    request, reason = controller.admit(source)
                    if request is None:
                        raise RuntimeError(reason)
                    admitted.append((controller, request))
                    ids = [view.id for view in views if view.camera_id == ident]
                    local = dict(options, spec={'view_ids': ids}, active_view_id=ids[0],
                                 recipe_name=self.recipe_name, capture_mode=self.capture_mode,
                                 software_button=False)
                    lane_views = [view for view in views if view.camera_id == ident]
                    if any(view.output_mode == 'AND' for view in lane_views):
                        local['require_all_views'] = True
                    elif lane_views[0].frame_source_view_id:
                        cached = self._frame_sources.get(ident, {})
                        local['seed_frames'] = dict(cached.get('frames', {}))
                        local['seed_frame_ids'] = dict(cached.get('ids', {}))
                    else:
                        self._frame_sources.pop(ident, None)
                    if source == 'pico':
                        # DUAL_CHANNEL_V1 physical inputs request host-owned
                        # acquisition in both modes. Reserve at the subsequent
                        # CAPTURE event, after this view's profile is ready.
                        local['capture_request_source'] = 'pico_request'
                    # Correlate all persisted images with this AND epoch.
                    epochs = {ticket.cycles[view.output_group] for view in views
                              if view.camera_id == ident and view.output_mode == 'AND'}
                    local['cycle_id'] = next(iter(epochs)) if len(epochs) == 1 else ticket.id
                    if not epochs:
                        output_groups = {view.output_group for view in lane_views}
                        if len(output_groups) == 1 and next(iter(output_groups)) is not None:
                            local['cycle_id'] = ticket.id + ':' + next(iter(output_groups))
                    jobs.append(CameraJob(ticket, ident, request, local))
                for view in views:
                    if view.external_trigger_mode == 'sequential' and source in ('pico', 'modbus'):
                        runtime = self.runtimes[view.camera_id]
                        runtime._external_sequence_index[source] = runtime._external_sequence_index.get(source, 0) + 1
                self._inflight.add(ticket.id)
                return jobs, ''
            except Exception as exc:
                for controller, request in admitted:
                    controller.finish(request, error=exc)
                self.groups.complete(ticket, {}, error=str(exc))
                return [], str(exc)

    def execute(self, job):
        runtime, controller = self.runtimes[job.camera_id], self.controllers[job.camera_id]
        try:
            pico = self.registry.picos[job.camera_id]
            live_camera = getattr(pico, '_live_camera', None)
            if live_camera is not None:
                pico.set_live_preview(False, live_camera)
            result = runtime.run(controller, job.request, job.options)
            from app.services.dual_pico_service import PicoChannel
            if isinstance(pico, PicoChannel):
                pico.wait_capture_idle()
            self._frame_sources[job.camera_id] = dict(frames=dict(result['captured_frames']), ids=dict(result['frame_ids']))
            return result
        except Exception as exc:
            self._frame_sources.pop(job.camera_id, None)
            if self.config.logging_enabled:
                from app.services.db_service import DbService
                database = DbService(self.db_path)
                try:
                    view_id = job.options['spec']['view_ids'][0]
                    metadata = dict(camera_id=job.camera_id,
                        camera_name=self.registry.names[job.camera_id],
                        camera_serial=self.registry.serials.get(job.camera_id),
                        pico_id=runtime.pico_id, cycle_id=job.options['cycle_id'],
                        request_id=job.request.id, view_id=view_id,
                        view_name=self.config.get_view(view_id).name,
                        capture_error=str(exc), per_tool=[])
                    database.insert_result(recipe_id=database.ensure_recipe(self.recipe_name),
                        ok=False, metrics={}, thumb_path='', full_path=None,
                        ts_ms=int(time.time() * 1000), meta_json=json.dumps(metadata),
                        view_id=view_id, run_id=job.options['cycle_id'])
                except Exception as storage_error:
                    exc = RuntimeError(f'{exc}; zápis chyby zlyhal: {storage_error}')
                finally:
                    database.close()
            return {'error': exc}

    def complete(self, jobs, results):
        # A manual AND request may contain both lanes; complete the shared ticket
        # once all its jobs have returned. Physical requests have one job each.
        statuses, errors_by_view, verdicts = {}, {}, {}
        for job, result in zip(jobs, results):
            error = result.get('error')
            if error:
                for ident in job.ticket.view_ids:
                    if self.groups.views[ident].camera_id == job.camera_id:
                        errors_by_view[ident] = str(error)
            else:
                statuses.update({record['view_id']: record['status'] for record in result['records']})
                for ident in job.ticket.view_ids:
                    view = self.groups.views[ident]
                    if view.camera_id == job.camera_id and view.output_mode == 'independent':
                        verdicts[view.output_group] = result['status']
        publication_error = None
        try:
            return self.groups.complete(jobs[0].ticket, statuses, errors_by_view=errors_by_view, verdicts=verdicts)
        except Exception as exc:
            publication_error = exc
            raise
        finally:
            for job, result in zip(jobs, results):
                self.controllers[job.camera_id].finish(job.request, error=result.get('error') or publication_error)
                from app.services.camera_pio import IncompletePioPair
                if (publication_error is None and isinstance(result.get('error'), IncompletePioPair) and self.capture_mode == 'trigger'
                        and not self._recovery_cancel.is_set()):
                    budget = self.controllers[job.camera_id].begin_recovery()
                    if budget:
                        self._recovering[job.camera_id] = (job, budget, result['error'])
            with self._admission_lock:
                self._inflight.discard(jobs[0].ticket.id)

    def recover(self, camera_id):
        from app.services.trigger_recovery import recover_trigger
        pending = self._recovering.get(camera_id)
        if pending is None:
            return
        job, budget, original = pending
        try:
            result = recover_trigger(self.runtimes[camera_id], job.request.id, original, budget,
                                     self._recovery_cancel, self.on_recovery)
            self.controllers[camera_id].finish_recovery(result)
            self.on_recovery(dict(event='recovery_finished', camera_id=camera_id, **result))
        finally:
            self._recovering.pop(camera_id, None)

    def cancel_recovery(self):
        self._recovery_cancel.set()

    def hardware_fault(self, reason):
        with self._admission_lock:
            self._hardware_fault = reason
        self.cancel_recovery()
        for camera in self.registry.cameras.values():
            pio = getattr(camera, 'pio', None)
            if pio is not None:
                pio._invalidate_pio()

    def quiesce(self):
        with self._admission_lock:
            if self._inflight or self._recovering:
                raise RuntimeError('Kamery ešte dokončujú kontrolu.')
        if self.groups:
            self.groups.close()
            self.groups.wait_publications()
        def stop_camera(item):
            ident, runtime = item
            if not self.controllers[ident].pause(runtime.quiesce):
                return self.controllers[ident].snapshot()['error']
            return None
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='camera-stop') as pool:
            errors = [error for error in pool.map(stop_camera, self.runtimes.items()) if error]
        if errors:
            raise RuntimeError('; '.join(errors))

    def shutdown(self):
        self.quiesce()
        self.registry.close()
        self.modbus.close()

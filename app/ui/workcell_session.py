"""Two bounded production lanes; all widget updates return to the Qt thread."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from PySide6.QtCore import QObject, Signal, Qt


class WorkcellSession(QObject):
    start_batch = Signal(object)
    delivered = Signal(object, object)
    outcome = Signal(object)
    rejected = Signal(str)
    result = Signal(object)
    idle = Signal()
    started = Signal()
    recovery = Signal(object)
    board_fault = Signal(str)

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='camera-inspection')
        self.pending = {}
        self._lock = RLock()
        self.start_batch.connect(self._start, Qt.QueuedConnection)
        self.delivered.connect(self._deliver, Qt.QueuedConnection)
        self.runtime.on_outcome = self.outcome.emit
        self.runtime.on_recovery = self.recovery.emit
        self.runtime.on_idle = self.idle.emit
        def failed(reason):
            self.runtime.hardware_fault(reason)
            self.board_fault.emit(reason)
        self.runtime.on_error = failed
        if self.runtime.registry.transport is not None:
            self.runtime.registry.transport.on_failure = failed

    def request(self, source, options):
        jobs, reason = self.runtime.admit(source, options)
        if not jobs:
            self.rejected.emit(reason)
            return False
        self.start_batch.emit(jobs)
        return True

    def _start(self, jobs):
        self.started.emit()
        with self._lock:
            self.pending[jobs[0].ticket.id] = dict(jobs=jobs, results={})
        for job in jobs:
            def work(current=job):
                self._finished(current, self.runtime.execute(current))
            self.pool.submit(work)

    def _finished(self, job, result):
        with self._lock:
            batch = self.pending[job.ticket.id]
            batch['results'][job.camera_id] = result
            self.delivered.emit(job, result)
            complete = len(batch['results']) == len(batch['jobs'])
            if complete:
                self.pending.pop(job.ticket.id)
        if complete:
            try:
                # PLC writes and SQLite commits stay on the production worker.
                self.runtime.complete(batch['jobs'], [batch['results'][item.camera_id] for item in batch['jobs']])
                for item in batch['jobs']:
                    self.runtime.recover(item.camera_id)
            except Exception as exc:
                self.rejected.emit(f'Výsledok skupiny sa nepodarilo odoslať/uložiť: {exc}')
            if not self.runtime.processing:
                self.idle.emit()

    def _deliver(self, job, result):
        if 'error' not in result:
            if job.ticket.cycles:
                outcomes = [self.runtime.last_outcomes.get(epoch) for epoch in job.ticket.cycles.values()]
                status = 'nok' if any(outcome and outcome.status == 'nok' for outcome in outcomes) else 'warn'
                result = dict(result, status=status, relevant_reports=[])
            self.result.emit(result)
        else:
            self.rejected.emit(f'{job.camera_id}: {result["error"]}')

    def shutdown(self):
        self.pool.shutdown(wait=False)

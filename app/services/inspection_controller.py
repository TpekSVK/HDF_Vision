"""Instance-owned production lifecycle and bounded trigger admission.

No Qt, camera singleton or process-wide queue. A request reserves the controller
before a UI/worker handoff; only its owner can complete it. Hardware callbacks
run outside the state lock. Execution adapters choose their own worker thread.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from threading import RLock
import time
import uuid


class InspectionState(str, Enum):
    PAUSED = 'paused'
    PREPARING = 'preparing'
    READY = 'ready'
    BUSY = 'busy'
    ERROR = 'error'
    CLOSED = 'closed'


@dataclass(frozen=True)
class InspectionRequest:
    id: str
    source: str
    received_at: float
    camera_id: str = "camera_1"


class InspectionController:
    def __init__(self, camera_id="camera_1"):
        self.camera_id = camera_id
        self._lock = RLock()
        self._state = InspectionState.PAUSED
        self._request = None
        self._error = ''
        self._counts = Counter()

    @property
    def state(self):
        with self._lock:
            return self._state

    def snapshot(self):
        with self._lock:
            return {'state': self._state.value, 'error': self._error,
                    'request_id': self._request.id if self._request else None,
                    'counts': dict(self._counts)}

    def prepare(self, prepare_hardware, quiesce):
        with self._lock:
            if self._state in {InspectionState.BUSY, InspectionState.PREPARING, InspectionState.CLOSED}:
                return False
            self._state = InspectionState.PREPARING
            self._error = ''
        try:
            prepare_hardware()
        except Exception as exc:
            try:
                quiesce()
            except Exception as cleanup:
                exc = RuntimeError(f'{exc}; zastavenie Pico: {cleanup}')
            with self._lock:
                self._state = InspectionState.ERROR
                self._error = str(exc)
                self._counts['preparation_failed'] += 1
            return False
        with self._lock:
            self._state = InspectionState.READY
        return True

    def pause(self, quiesce, *, close=False):
        with self._lock:
            if self._state in {InspectionState.BUSY, InspectionState.PREPARING}:
                return False
            if self._state == InspectionState.CLOSED:
                return True
            # Close admission before touching hardware.
            self._state = InspectionState.PREPARING
        try:
            quiesce()
        except Exception as exc:
            with self._lock:
                self._state = InspectionState.ERROR
                self._error = str(exc)
            return False
        with self._lock:
            self._state = InspectionState.CLOSED if close else InspectionState.PAUSED
        return True

    def admit(self, source):
        with self._lock:
            self._counts['received'] += 1
            if self._state != InspectionState.READY:
                reason = self._state.value
                self._counts[f'rejected_{reason}'] += 1
                return None, reason
            request = InspectionRequest(uuid.uuid4().hex, str(source), time.monotonic(), self.camera_id)
            self._request = request
            self._state = InspectionState.BUSY
            self._counts['accepted'] += 1
            return request, None

    def owns(self, request):
        with self._lock:
            return request is not None and request is self._request

    def finish(self, request, *, error=None):
        with self._lock:
            if request is not self._request or request is None:
                raise ValueError('Výsledok nepatrí aktívnej požiadavke.')
            self._request = None
            self._counts['failed' if error else 'completed'] += 1
            self._state = InspectionState.ERROR if error else InspectionState.READY
            self._error = str(error or '')

    def run_sequence(self, request, trigger_state, execute_view, finalize):
        """Drive finite view branches independently of MainWindow/Qt."""
        if not self.owns(request):
            raise ValueError('Požiadavka nemá rezervovaný kontrolér.')
        trigger_state['request_id'] = request.id
        trigger_state['request_received_at'] = request.received_at
        queue = list(trigger_state['views_to_process'])
        visited = set()
        while queue:
            spec = queue.pop(0)
            view = spec['view']
            key = getattr(view, 'id', None) or spec['index']
            if key in visited:
                raise ValueError(f'Cyklus vetvenia opakuje pohľad {key}.')
            visited.add(key)
            outcome = execute_view(spec, trigger_state)
            if outcome.get('replace_queue') is not None:
                queue = list(outcome['replace_queue'])
            if outcome.get('should_break'):
                break
        finalize(trigger_state)

"""Bounded camera admission and timed AND results, without widgets or hardware.

No completed camera can be admitted again while its AND group awaits a peer.
Tickets carry an epoch: late results after a timeout never complete a new cycle.
"""
from dataclasses import dataclass
from threading import Condition, RLock, Timer
import time
import uuid

from app.services.dual_camera_rules import validate_view_routing


@dataclass(frozen=True)
class GroupTicket:
    id: str
    view_ids: tuple[str, ...]
    cameras: frozenset[str]
    cycles: dict[str, str]


@dataclass(frozen=True)
class GroupOutcome:
    group_id: str | None
    cycle_id: str
    status: str
    reason: str
    view_statuses: dict[str, str]
    missing_views: tuple[str, ...]
    started_at: float
    finished_at: float


class InspectionGroups:
    def __init__(self, views, publish, *, clock=time.monotonic, timers=True, on_idle=None, on_error=None):
        self.groups = validate_view_routing(views, complete=True)
        self.views = {view.id: view.copy() for view in views}
        self.publish = publish
        self.clock, self.timers = clock, timers
        self._lock = RLock()
        self._settled = Condition(self._lock)
        self._active = {}
        self._jobs = {}
        self._holds = {}
        self._closed = False
        self._publishing = 0
        self._pending_groups = {}
        self.on_idle = on_idle or (lambda: None)
        self.on_error = on_error or (lambda error: None)

    @property
    def busy(self):
        with self._lock:
            return bool(self._jobs or self._active or self._publishing)

    @property
    def processing(self):
        with self._lock:
            return bool(self._jobs or self._publishing)

    def admit(self, view_ids):
        ids = tuple(dict.fromkeys(view_ids))
        with self._lock:
            if self._closed:
                return None, 'kontroly sú zastavené'
            if not ids or any(ident not in self.views for ident in ids):
                return None, 'neplatný pohľad'
            cameras = frozenset(self.views[ident].camera_id for ident in ids)
            if any(self._pending_groups.get(self.views[ident].output_group) for ident in ids):
                return None, 'výstupná skupina ešte odosiela predchádzajúci výsledok'
            if any(cameras & job.cameras for job in self._jobs.values()):
                return None, 'kamera práve spracováva snímku'
            if any(self._holds.get(camera) for camera in cameras):
                return None, 'kamera už dodala snímku do AND; čaká na dokončenie skupiny'
            cycles = {}
            for ident in ids:
                view = self.views[ident]
                if view.output_mode != 'AND':
                    continue
                group = view.output_group
                state = self._active.get(group)
                if state is None:
                    now = self.clock()
                    state = dict(id=uuid.uuid4().hex, started=now,
                                 deadline=now + view.and_timeout_ms / 1000,
                                 accepted=set(), statuses={}, timer=None)
                    self._active[group] = state
                    if self.timers:
                        timer = Timer(view.and_timeout_ms / 1000, self.expire)
                        timer.daemon = True
                        state['timer'] = timer
                        timer.start()
                state['accepted'].add(ident)
                cycles[group] = state['id']
            ticket = GroupTicket(uuid.uuid4().hex, ids, cameras, cycles)
            self._jobs[ticket.id] = ticket
            # Hold cameras from admission, not only after completion; other
            # groups must not steal a camera between acquisition and evaluation.
            for camera in cameras:
                for group, epoch in cycles.items():
                    if any(self.views[ident].camera_id == camera and
                           self.views[ident].output_group == group for ident in ids):
                        self._holds.setdefault(camera, set()).add((group, epoch))
            return ticket, ''

    def _finish_group(self, group, state, *, reason=''):
        state['timer'] and state['timer'].cancel()
        self._active.pop(group, None)
        for holds in self._holds.values():
            holds.discard((group, state['id']))
        required = {view.id for view in self.groups[group]}
        missing = tuple(sorted(required - state['statuses'].keys()))
        status = ('nok' if reason or missing or any(value != 'ok' for value in state['statuses'].values())
                  else 'ok')
        return self._reserve_publication(GroupOutcome(group, state['id'], status, reason,
                            dict(state['statuses']), missing, state['started'], self.clock()))

    def _reserve_publication(self, outcome):
        self._publishing += 1
        self._pending_groups[outcome.group_id] = self._pending_groups.get(outcome.group_id, 0) + 1
        return outcome

    def _emit(self, outcomes):
        first_error = None
        for outcome in outcomes:
            try:
                self.publish(outcome)
            except Exception as exc:
                first_error = first_error or exc
                self.on_error(f'Publikovanie výsledku zlyhalo: {exc}')
            finally:
                with self._lock:
                    self._publishing -= 1
                    self._pending_groups[outcome.group_id] -= 1
                    if not self._pending_groups[outcome.group_id]:
                        self._pending_groups.pop(outcome.group_id)
                    self._settled.notify_all()
        if outcomes:
            self.on_idle()
        if first_error is not None:
            raise first_error

    def wait_publications(self, timeout=125):
        with self._settled:
            if not self._settled.wait_for(lambda: not self._publishing, timeout):
                raise RuntimeError('Odoslanie výsledku skupiny sa nedokončilo.')

    def complete(self, ticket, statuses, *, error='', verdicts=None, errors_by_view=None):
        outcomes = []
        with self._lock:
            if self._jobs.pop(ticket.id, None) is not ticket:
                return []
            independent = {}
            for ident in ticket.view_ids:
                view = self.views[ident]
                view_error = error or (errors_by_view or {}).get(ident, '')
                value = 'nok' if view_error else str(statuses.get(ident, 'nok')).lower()
                if value not in ('ok', 'nok', 'warn'):
                    value = 'nok'
                if view.output_mode == 'AND':
                    group = view.output_group
                    state = self._active.get(group)
                    if state is not None and state['id'] == ticket.cycles.get(group):
                        state['statuses'][ident] = value
                else:
                    independent.setdefault(view.output_group, {})[ident] = value
            for group, epoch in ticket.cycles.items():
                state = self._active.get(group)
                if state is None or state['id'] != epoch:
                    continue
                required = {view.id for view in self.groups[group]}
                group_error = error or '; '.join(str(value) for ident, value in (errors_by_view or {}).items() if ident in required)
                if self.clock() >= state['deadline']:
                    outcomes.append(self._finish_group(group, state, reason='Vypršal časový limit AND.'))
                elif group_error:
                    outcomes.append(self._finish_group(group, state, reason=str(group_error)))
                elif required <= state['statuses'].keys():
                    outcomes.append(self._finish_group(group, state))
            for group, values in independent.items():
                now = self.clock()
                group_error = error or '; '.join(str(value) for ident, value in (errors_by_view or {}).items() if ident in values)
                status = (verdicts or {}).get(group)
                if status is None or group_error:
                    status = 'nok' if group_error or any(value != 'ok' for value in values.values()) else 'ok'
                outcomes.append(self._reserve_publication(GroupOutcome(group, ticket.id + (':' + group if group else ''), status, str(group_error), values, (), now, now)))
        self._emit(outcomes)
        return outcomes

    def expire(self):
        outcomes = []
        with self._lock:
            for group, state in list(self._active.items()):
                if self.clock() >= state['deadline']:
                    outcomes.append(self._finish_group(group, state, reason='Vypršal časový limit AND.'))
        self._emit(outcomes)
        return outcomes

    def close(self):
        outcomes = []
        with self._lock:
            self._closed = True
            for group, state in list(self._active.items()):
                outcomes.append(self._finish_group(group, state, reason='Kontrola bola zastavená.'))
        self._emit(outcomes)
        return outcomes

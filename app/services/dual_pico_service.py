"""Two channel-local Pico services over one exclusively owned USB connection.

Command locks, CAPTURE reservations, LIVE state and replies belong to a channel.
Only writes to the physical wire are serialized; camera 2 can issue a command
while camera 1 awaits its hardware completion. The firmware tags every line.
"""
import re
import threading

from app.services.pico_service import PicoService, _DISCONNECTED


class _ChannelSerial:
    def __init__(self, transport, channel):
        self.transport, self.channel = transport, channel

    @property
    def is_open(self):
        dev = self.transport._serial
        return dev is not None and dev.is_open

    def write(self, data):
        with self.transport._wire_lock:
            dev = self.transport._serial
            if dev is None:
                raise OSError('Pico je odpojené.')
            dev.write(f'CH {self.channel} '.encode() + data)
            dev.flush()

    def flush(self):
        pass  # write + flush above are one atomic operation on the shared wire


class PicoChannel(PicoService):
    def __init__(self, transport, channel):
        super().__init__()
        self.transport, self.channel = transport, channel

    def connect(self):
        if not self.transport.connect():
            self.last_error = self.transport.last_error
            return False
        with self._state_lock:
            self._serial = _ChannelSerial(self.transport, self.channel)
            self._rx_thread = self.transport._rx_thread
            self._active_port = self.transport._active_port
            self._available = True
        return True

    def supports_pio(self):
        if self._pio_capable is None:
            ok, response = self._send_command('STATUS')
            if not ok and not response.startswith('ERR UNKNOWN'):
                raise RuntimeError('Spojenie s Picom zlyhalo: ' + (self.last_error or response or 'bez odpovede'))
            firmware = re.search(r'FIRMWARE pico_hdf_controller (\d+)\.(\d+)(?:\.(\d+))?', response)
            version = tuple(int(part or 0) for part in firmware.groups()) if firmware else (0, 0, 0)
            if not ok or 'DUAL_CHANNEL_V1' not in response.split() or version < (4, 3, 1):
                raise RuntimeError('Dva kamerové kanály vyžadujú Pico firmware 4.3.1 alebo novší. Aktualizujte Pico.')
            self._pio_capable = 'CAPABILITIES PIO_PAIR_V1 SESSION_V1' in response
        return self._pio_capable

    def close(self):
        # The workcell owns the transport. Closing one endpoint never closes
        # the peer camera's USB connection or turns off its outputs.
        self._disconnect('Kamerový kanál bol uzavretý.')

    def wait_capture_idle(self):
        if not self._capture_request_lock.acquire(timeout=125.0):
            raise RuntimeError('Pico nedokončilo predchádzajúce snímanie.')
        self._capture_request_lock.release()
        error = (getattr(self, '_last_master_result', {}).get('error')
                 if self._session_mode == 'MASTER' else None)
        if error:
            raise RuntimeError(str(error))

    def _disconnect(self, reason):
        with self._state_lock:
            pending = self._pending_response
            self._available = False
            self._serial = None
            self._rx_thread = None
            self._pio_capable = None
            self._session_mode = None
            self._capture_receiver = None
            self.last_error = reason
        if pending is not None:
            pending.put(_DISCONNECTED)

    def receive(self, line):
        request = re.fullmatch(r'REQUEST\s+(IN[1-8]|V[12])', line, re.I)
        if request:
            if self._session_mode in ('MASTER', 'TRIGGER'):
                self._dispatch_trigger(request[1].upper())
            return
        capture = self._parse_capture(line)
        if capture is not None:
            if self._session_mode != 'MASTER':
                return
            with self._state_lock:
                receiver = self._capture_receiver
                if receiver is not None and receiver[0] == capture:
                    self._capture_receiver = None
                else:
                    receiver = None
            if receiver is not None:
                try:
                    receiver[1]()
                except Exception as exc:
                    self.last_error = str(exc)
                    with self._state_lock:
                        pending = self._pending_response
                    if pending is not None:
                        pending.put(_DISCONNECTED)
            else:
                self._dispatch_trigger(capture)
            return
        with self._state_lock:
            pending, command = self._pending_response, self._pending_command
        if pending is not None and self._line_matches_command(line, command):
            pending.put(line)


class DualPicoTransport(PicoService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wire_lock = threading.Lock()
        self.channels = {i: PicoChannel(self, i) for i in (1, 2)}
        self.on_failure = lambda reason: None

    def _rx_loop(self, dev, stop):
        reason = 'Pico je odpojené.'
        try:
            while not stop.is_set():
                raw = dev.readline()
                if not raw:
                    continue
                line = raw.decode('utf-8', errors='replace').strip()
                if line.startswith('READY pico_hdf_controller '):
                    was_active = any(channel._session_mode in ('MASTER', 'TRIGGER') for channel in self.channels.values())
                    reason = 'Pico sa reštartovalo; znovu pripravte RUN.'
                    for channel in self.channels.values():
                        channel._disconnect(reason)
                    if was_active:
                        self.on_failure(reason)
                    continue
                match = re.fullmatch(r'CH ([12]) (.*)', line)
                if match:
                    self.channels[int(match[1])].receive(match[2])
                elif line.startswith('ERR'):
                    # Old firmware cannot parse CH. Wake both waiters with an
                    # actionable capability error instead of timing out silently.
                    for channel in self.channels.values():
                        channel.receive(line)
        except Exception as exc:
            reason = str(exc)
        finally:
            with self._state_lock:
                if self._serial is dev:
                    self._serial = None
                    self._available = False
                    self._rx_thread = None
                    self._rx_stop = None
                    self.last_error = reason
            for channel in self.channels.values():
                channel._disconnect(reason)
            dev.close()
            if not stop.is_set():
                self.on_failure(reason)

    def close(self):
        super().close()
        for channel in self.channels.values():
            channel._disconnect('Pico spojenie bolo uzavreté.')

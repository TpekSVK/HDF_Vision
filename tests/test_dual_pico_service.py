from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import pico_service
from app.services.dual_pico_service import DualPicoTransport
from test_pico_service import FakeSerial, wait_for


@pytest.fixture
def dual(monkeypatch):
    dev = FakeSerial(timeout=.02)
    monkeypatch.setattr(pico_service, 'serial', SimpleNamespace(Serial=lambda *a, **kw: dev))
    transport = DualPicoTransport(port='/dev/test', timeout_s=.1)
    assert transport.connect()
    yield transport, dev
    transport.close()


def test_commands_can_be_in_flight_on_both_channels_and_replies_do_not_cross(dual):
    transport, dev = dual
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(transport.channels[1]._send_command, 'STATUS')
        second = pool.submit(transport.channels[2]._send_command, 'STATUS')
        assert wait_for(lambda: len(dev.writes) == 2)
        dev.emit('CH 2 MANUAL_LIGHT ON', 'CH 1 MANUAL_LIGHT OFF', 'CH 2 END', 'CH 1 END')
        assert first.result() == (True, 'MANUAL_LIGHT OFF\nEND')
        assert second.result() == (True, 'MANUAL_LIGHT ON\nEND')
    assert set(dev.writes) == {b'CH 1 STATUS\n', b'CH 2 STATUS\n'}


def test_channel_events_and_capture_reservations_are_isolated(dual):
    transport, dev = dual
    one, two = transport.channels.values()
    one._session_mode, two._session_mode = 'MASTER', 'TRIGGER'
    received, reservation = [], []
    one.register_trigger_callback(lambda value: received.append((1, value)))
    two.register_trigger_callback(lambda value: received.append((2, value)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(one._send_command, 'FIRE V1',
                              capture_receiver=('V1', lambda: reservation.append(1)))
        assert wait_for(lambda: bool(dev.writes))
        dev.emit('CH 2 CAPTURE V1', 'CH 2 REQUEST IN3', 'CH 1 CAPTURE V1', 'CH 1 OK FIRED V1')
        assert pending.result()[0]
    assert reservation == [1] and received == [(2, 'IN3')]


def test_master_physical_input_requests_host_owned_acquisition(dual):
    transport, dev = dual
    channel = transport.channels[1]
    channel._session_mode = 'MASTER'
    events = []
    channel.register_trigger_callback(events.append)
    dev.emit('CH 1 REQUEST IN2')
    assert wait_for(lambda: events == ['IN2'])


def test_closing_channel_does_not_close_peer(dual):
    transport, dev = dual
    for channel in transport.channels.values():
        channel.connect()
    transport.channels[1].close()
    assert dev.is_open and transport.channels[2].is_available()


def test_old_firmware_is_rejected_with_upgrade_message(dual):
    transport, dev = dual
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(transport.channels[1].supports_pio)
        assert wait_for(lambda: bool(dev.writes))
        dev.emit('ERR UNKNOWN')
        with pytest.raises(RuntimeError, match='4.3'):
            pending.result()


def test_connection_failure_is_not_reported_as_old_firmware(dual, monkeypatch):
    transport, _ = dual
    channel = transport.channels[1]
    channel.last_error = 'Could not exclusively lock port'
    monkeypatch.setattr(channel, '_send_command', lambda command: (False, ''))
    with pytest.raises(RuntimeError, match='exclusively lock'):
        channel.supports_pio()


def test_board_restart_invalidates_both_sessions_and_pending_reply(dual):
    transport, dev = dual
    one, two = transport.channels.values()
    one._session_mode = two._session_mode = 'TRIGGER'
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(one._send_command, 'PIO FIRE 99 2 16670 100 0 20000 1 50000')
        assert wait_for(lambda: bool(dev.writes))
        dev.emit('READY pico_hdf_controller 4.3-dual-channel CHANNELS=2')
        assert pending.result()[0] is False
    assert one._session_mode is None and two._session_mode is None

"""An early launcher must release GNOME startup and never hold up logout."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('gnome_startup', Path(__file__).parents[1] / 'tools/gnome_startup.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


def setup_bus(monkeypatch):
    monkeypatch.setenv('DESKTOP_AUTOSTART_ID', 'startup-token')
    bus = Mock()
    gio = SimpleNamespace(bus_get=Mock(), bus_get_finish=Mock(return_value=bus),
        BusType=SimpleNamespace(SESSION=1), DBusCallFlags=SimpleNamespace(NONE=0),
        DBusSignalFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Error=RuntimeError, Variant=lambda signature, value: (signature, value),
        VariantType=SimpleNamespace(new=lambda signature: signature))
    stop = Mock(); log = Mock()
    session.register_startup(gio, glib, log, stop)
    gio.bus_get.call_args.args[2](None, 'connection')
    return bus, log, stop


def test_desktop_launch_has_no_session_registration(monkeypatch):
    monkeypatch.delenv('DESKTOP_AUTOSTART_ID', raising=False)
    gio = Mock()
    session.register_startup(gio, Mock(), Mock(), Mock())
    gio.bus_get.assert_not_called()


def test_startup_registration_consumes_token_and_responds_to_logout(monkeypatch):
    bus, log, stop = setup_bus(monkeypatch)
    assert 'DESKTOP_AUTOSTART_ID' not in session.os.environ
    args = bus.call.call_args.args
    assert args[3:5] == ('RegisterClient', ('(ss)', ('HDF_Vision', 'startup-token')))
    assert args[7] == 2000
    bus.call_finish.return_value.unpack.return_value = ('/client/1',)
    args[-1](bus, 'registered')
    callback = bus.signal_subscribe.call_args.args[-1]
    for signal in ('QueryEndSession', 'EndSession'):
        callback(bus, None, '/client/1', None, signal, None)
        assert bus.call.call_args.args[3:5] == ('EndSessionResponse', ('(bs)', (True, '')))
    callback(bus, None, '/client/1', None, 'Stop', None)
    stop.assert_called_once()


def test_failed_registration_does_not_interrupt_launcher(monkeypatch):
    bus, log, stop = setup_bus(monkeypatch)
    bus.call_finish.side_effect = RuntimeError('No session')
    bus.call.call_args.args[-1](bus, 'failed')
    log.warning.assert_called_once()
    bus.signal_subscribe.assert_not_called()
    stop.assert_not_called()

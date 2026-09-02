import queue
import threading
import time

import pytest

from app.services import pico_service


class FakeSerial:
    def __init__(self, *_args, timeout=0.05, **_kwargs):
        self.timeout = timeout
        self.is_open = True
        self.writes = []
        self.rx = queue.Queue()

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass

    def readline(self):
        try:
            return self.rx.get(timeout=min(self.timeout, 0.02))
        except queue.Empty:
            return b""

    def emit(self, *lines):
        for line in lines:
            self.rx.put((line + "\n").encode())

    def close(self):
        self.is_open = False


@pytest.fixture
def connected_service(monkeypatch):
    devices = []

    class SerialFactory:
        def Serial(self, *args, **kwargs):
            device = FakeSerial(*args, **kwargs)
            devices.append(device)
            return device

    monkeypatch.setattr(pico_service, "serial", SerialFactory())
    service = pico_service.PicoService(port="/dev/ttyACM0", timeout_s=0.08)
    assert service.connect()
    yield service, devices[0]
    service.close()


def wait_for(predicate, timeout=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("CAPTURE IN1", [1]),
        ("capture in8", [8]),
        ("CAPTURE IN9", []),
        ("CAPTURE IN0", []),
        ("CAPTURE", []),
        ("some other text", []),
    ],
)
def test_capture_event_parsing(connected_service, line, expected):
    service, device = connected_service
    received = []
    service.register_trigger_callback(received.append)

    device.emit(line)

    if expected:
        assert wait_for(lambda: received == expected)
    else:
        time.sleep(0.04)
        assert received == []


def test_callback_failure_does_not_stop_reader(connected_service):
    service, device = connected_service
    received = []

    def broken(_index):
        raise RuntimeError("broken callback")

    service.register_trigger_callback(broken)
    service.register_trigger_callback(received.append)
    device.emit("CAPTURE IN2", "CAPTURE IN3")

    assert wait_for(lambda: received == [2, 3])


@pytest.mark.parametrize(
    ("response", "expected_ok"),
    [("OK SAVED", True), ("ERR UNKNOWN", False)],
)
def test_simple_command_response(connected_service, response, expected_ok):
    service, device = connected_service
    device.emit(response)

    ok, text = service._send_command("SAVE")

    assert ok is expected_ok
    assert text == response
    assert device.writes == [b"SAVE\n"]


def test_command_timeout(connected_service):
    service, _device = connected_service

    ok, response = service._send_command("SAVE")

    assert not ok
    assert response == ""
    assert "No response" in service.last_error


@pytest.mark.parametrize(
    ("command", "lines"),
    [
        ("STATUS", ["FIRMWARE pico_hdf_controller 3.2.0", "V1_MODE MASTER", "END"]),
        ("INPUTS", ["INPUTS IN1=OFF IN2=ACTIVE", "END"]),
    ],
)
def test_multiline_response(connected_service, command, lines):
    service, device = connected_service
    device.emit(*lines)

    ok, response = service._send_command(command)

    assert ok
    assert response == "\n".join(lines)


def test_inputs_public_api(connected_service):
    service, device = connected_service
    device.emit("INPUTS IN1=ACTIVE IN2=OFF", "END")

    assert service.inputs() == "INPUTS IN1=ACTIVE IN2=OFF\nEND"


@pytest.mark.parametrize(("enabled", "command"), [(True, "LIGHT ON"), (False, "LIGHT OFF")])
def test_set_manual_light_sends_command(monkeypatch, enabled, command):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK"))

    assert service.set_manual_light(enabled)
    assert sent == [command]


def test_set_manual_light_failure_is_reported(monkeypatch):
    service = pico_service.PicoService()
    monkeypatch.setattr(service, "_send_command", lambda _value: (False, "ERR LIGHT"))

    assert not service.set_manual_light(True)


@pytest.mark.parametrize(
    ("line", "expected"),
    [("MANUAL_LIGHT ON", True), ("manual_light off", False), ("", None)],
)
def test_parse_manual_light_status(line, expected):
    assert pico_service.PicoService.parse_status_config(line)["manual_light"] is expected


@pytest.mark.parametrize(
    ("view", "mode", "command"),
    [("V1", "MASTER", "SET V1 MODE MASTER"), ("V2", "trigger", "SET V2 MODE TRIGGER")],
)
def test_set_view_mode_sends_normalized_command(monkeypatch, view, mode, command):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK SET"))

    assert service.set_view_mode(view, mode)
    assert sent == [command]


@pytest.mark.parametrize(
    ("view", "mode"),
    [("V3", "MASTER"), ("V1", "AUTO"), ("", "MASTER"), (None, "MASTER"), ("V1", None)],
)
def test_set_view_mode_rejects_invalid_values_without_sending(monkeypatch, view, mode):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK SET"))

    assert not service.set_view_mode(view, mode)
    assert sent == []
    assert service.last_error


def test_save_and_save_config_remain_compatible(monkeypatch):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK SAVED"))

    assert service.save()
    assert service.save_config()
    assert sent == ["SAVE", "SAVE"]


@pytest.mark.parametrize("method,args,command", [
    ("set_profile_delay", ("V1", 10), "SET V1 DELAY 10"),
    ("set_profile_pulse", ("V1", 200), "SET V1 PULSE 200"),
    ("set_profile_capture", ("V1", 30), "SET V1 CAPTURE 30"),
    ("map_input", (1, "V1"), "MAP IN1 V1"),
    ("map_input", (7, "v2"), "MAP IN7 V2"),
    ("map_input", (8, "OFF"), "MAP IN8 OFF"),
])
def test_configuration_commands(monkeypatch, method, args, command):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK"))
    assert getattr(service, method)(*args)
    assert sent == [command]


@pytest.mark.parametrize("method,args", [
    ("set_profile_delay", ("V1", -1)), ("set_profile_delay", ("V3", 0)),
    ("set_profile_pulse", ("V1", 0)), ("set_profile_capture", ("V2", -1)),
    ("map_input", (0, "V1")), ("map_input", (9, "V2")), ("map_input", (1, "AUTO")),
])
def test_configuration_validation_does_not_send(monkeypatch, method, args):
    service = pico_service.PicoService()
    sent = []
    monkeypatch.setattr(service, "_send_command", lambda value: (sent.append(value) or True, "OK"))
    assert not getattr(service, method)(*args)
    assert sent == []
    assert service.last_error


def test_parse_status_config_is_tolerant_and_does_not_invent_values():
    response = """v1_mode master
V1_DELAY 0
V1_PULSE 100
V1_CAPTURE 20
V2_MODE MASTER
V2_DELAY 50
V2_PULSE 300
V2_CAPTURE 80
input_map in1=v1 IN5=bad in7=V2 IN8=off
END"""
    assert pico_service.PicoService.parse_status_config(response) == {
        "V1": {"mode": "MASTER", "delay_ms": 0, "pulse_ms": 100, "capture_ms": 20},
        "V2": {"mode": "MASTER", "delay_ms": 50, "pulse_ms": 300, "capture_ms": 80},
        "input_map": {1: "V1", 7: "V2", 8: "OFF"},
        "manual_light": None,
    }
    assert pico_service.PicoService.parse_status_config("") == {
        "V1": {}, "V2": {}, "input_map": {}, "manual_light": None
    }
    assert pico_service.PicoService.parse_status_config("V1_DELAY nope\nV2_MODE AUTO") == {
        "V1": {}, "V2": {}, "input_map": {}, "manual_light": None
    }


def test_capture_is_separated_from_status_response(connected_service):
    service, device = connected_service
    received = []
    service.register_trigger_callback(received.append)
    device.emit("FIRMWARE pico_hdf_controller", "CAPTURE IN3", "V1_MODE MASTER", "END")

    ok, response = service._send_command("STATUS")

    assert ok
    assert received == [3]
    assert response == "FIRMWARE pico_hdf_controller\nV1_MODE MASTER\nEND"
    assert "CAPTURE" not in response


def test_unsolicited_fired_line_is_ignored(connected_service):
    service, device = connected_service
    received = []
    service.register_trigger_callback(received.append)
    device.emit("OK FIRED V1 MODE=MASTER SOURCE=IN1")
    time.sleep(0.04)

    device.emit("OK SAVED")
    assert service._send_command("SAVE") == (True, "OK SAVED")
    assert received == []


def test_unsolicited_fired_line_does_not_steal_save_response(connected_service):
    service, device = connected_service
    result = []
    command = threading.Thread(target=lambda: result.append(service._send_command("SAVE")))
    command.start()
    assert wait_for(lambda: service._pending_response is not None)

    device.emit("OK FIRED V1 MODE=MASTER SOURCE=IN1", "OK SAVED")
    command.join(timeout=0.5)

    assert result == [(True, "OK SAVED")]


def test_close_stops_reader_and_marks_service_unavailable(connected_service):
    service, device = connected_service
    reader = service._rx_thread

    service.close()

    assert reader is not None and not reader.is_alive()
    assert not device.is_open
    assert not service.is_available()
    service.close()


def test_close_unblocks_waiting_command(connected_service):
    service, _device = connected_service
    result = []
    command = threading.Thread(target=lambda: result.append(service._send_command("SAVE")))
    command.start()
    assert wait_for(lambda: service._pending_response is not None)

    service.close()
    command.join(timeout=0.5)

    assert not command.is_alive()
    assert result and result[0][0] is False

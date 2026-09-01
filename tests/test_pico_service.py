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

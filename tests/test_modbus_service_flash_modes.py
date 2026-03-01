from app.services.modbus_service import ModbusConfig, ModbusService


def test_pulse_configured_flashes_keeps_flash_on_for_negative_delay(tmp_path):
    svc = ModbusService(config_path=tmp_path / "modbus_config.json")
    cfg = ModbusConfig(
        enabled=True,
        flash1_coil=5,
        flash1_delay_ms=-1,
        flash1_pulse_ms=120,
        flash2_coil=-1,
    )
    svc.set_config(cfg, persist=False)

    calls: list[tuple[str, int, bool]] = []

    def fake_write(address: int, value: bool, config: ModbusConfig) -> bool:
        calls.append(("write", int(address), bool(value)))
        return True

    def fake_pulse(*args, **kwargs):
        calls.append(("pulse", int(args[0]), True))
        return True

    svc._write_coil = fake_write  # type: ignore[method-assign]
    svc.pulse_coil = fake_pulse  # type: ignore[method-assign]

    svc.pulse_configured_flashes()

    assert calls == [("write", 5, True)]


def test_pulse_configured_flashes_uses_pulse_for_non_negative_delay(tmp_path):
    svc = ModbusService(config_path=tmp_path / "modbus_config.json")
    cfg = ModbusConfig(
        enabled=True,
        flash1_coil=3,
        flash1_delay_ms=50,
        flash1_pulse_ms=100,
        flash2_coil=4,
        flash2_delay_ms=0,
        flash2_pulse_ms=110,
    )
    svc.set_config(cfg, persist=False)

    pulse_calls: list[tuple[int, int, int]] = []

    def fake_pulse(address: int, *, pulse_ms=None, delay_ms=0, config=None):
        pulse_calls.append((int(address), int(delay_ms), int(pulse_ms)))
        return True

    svc.pulse_coil = fake_pulse  # type: ignore[method-assign]
    svc._write_coil = lambda *args, **kwargs: True  # type: ignore[method-assign]

    svc.pulse_configured_flashes()

    assert pulse_calls == [(3, 50, 100), (4, 0, 110)]

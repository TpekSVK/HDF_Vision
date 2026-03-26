from app.services.modbus_service import ModbusConfig


def test_modbus_config_ignores_legacy_flash_fields() -> None:
    cfg = ModbusConfig.from_dict(
        {
            "flash1_coil": 5,
            "flash1_delay_ms": 10,
            "flash1_pulse_ms": 200,
            "flash2_coil": 6,
            "flash2_delay_ms": 20,
            "flash2_pulse_ms": 250,
            "ok_coil": 2,
        }
    )

    assert cfg.ok_coil == 2
    assert not hasattr(cfg, "flash1_coil")
    assert not hasattr(cfg, "flash2_coil")


def test_modbus_config_to_dict_excludes_flash_fields() -> None:
    payload = ModbusConfig().to_dict()

    assert "flash1_coil" not in payload
    assert "flash2_coil" not in payload
    assert "flash1_delay_ms" not in payload
    assert "flash2_delay_ms" not in payload
    assert "flash1_pulse_ms" not in payload
    assert "flash2_pulse_ms" not in payload

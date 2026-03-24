from app.services.modbus_service import ModbusConfig, ModbusService


def test_modbus_config_from_dict_maps_legacy_trigger_to_first_input():
    cfg = ModbusConfig.from_dict({"trigger_di": 12})
    assert cfg.request_di_addresses == [12, -1, -1, -1, -1, -1, -1, -1]
    assert cfg.trigger_di == 12


def test_modbus_config_from_dict_prefers_new_request_input_list():
    cfg = ModbusConfig.from_dict({"trigger_di": 12, "request_di_addresses": [3, 4]})
    assert cfg.request_di_addresses == [3, 4, -1, -1, -1, -1, -1, -1]
    assert cfg.trigger_di == 3


def test_read_configured_discrete_inputs_returns_disabled_entries(tmp_path):
    svc = ModbusService(config_path=tmp_path / "modbus_config.json")
    cfg = ModbusConfig(enabled=True, request_di_addresses=[0, 1, -1, -1, -1, -1, -1, -1])

    def fake_read_discrete_input(address: int, *, config=None):
        return int(address) == 1

    svc.read_discrete_input = fake_read_discrete_input  # type: ignore[method-assign]
    results = svc.read_configured_discrete_inputs(config=cfg)

    assert len(results) == 8
    assert results[0]["disabled"] is False
    assert results[0]["value"] is False
    assert results[1]["disabled"] is False
    assert results[1]["value"] is True
    assert results[2]["disabled"] is True
    assert results[2]["value"] is None

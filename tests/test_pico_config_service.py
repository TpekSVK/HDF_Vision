import json

import pytest

from app.services.pico_config_service import PicoConfigService


def test_missing_config_has_safe_empty_default(tmp_path):
    service = PicoConfigService(tmp_path / "pico.json")
    assert service.get_enabled_inputs() == set()


def test_enabled_inputs_save_and_load(tmp_path):
    path = tmp_path / "pico.json"
    PicoConfigService(path).set_enabled_inputs({1, 3, 8})

    assert PicoConfigService(path).get_enabled_inputs() == {1, 3, 8}
    assert json.loads(path.read_text()) == {"enabled_inputs": [1, 3, 8]}


@pytest.mark.parametrize("invalid", [{0}, {9}, {-1}, {"abc"}, {True}])
def test_invalid_values_are_rejected(tmp_path, invalid):
    service = PicoConfigService(tmp_path / "pico.json")
    with pytest.raises(ValueError):
        service.set_enabled_inputs(invalid)


def test_invalid_stored_values_fall_back_to_safe_default(tmp_path):
    path = tmp_path / "pico.json"
    path.write_text('{"enabled_inputs": [1, 9]}')
    assert PicoConfigService(path).get_enabled_inputs() == set()


def test_is_input_enabled(tmp_path):
    service = PicoConfigService(tmp_path / "pico.json")
    service.set_enabled_inputs({3})
    assert service.is_input_enabled(3)
    assert not service.is_input_enabled(2)

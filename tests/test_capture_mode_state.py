import json
from app.services.capture_mode_state import load_capture_mode, save_capture_mode


def test_capture_mode_survives_restart_without_overwriting_recipe_choice(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'last_recipe': 'test2', 'other_setting': 42}))
    assert load_capture_mode(path) is None
    save_capture_mode(path, 'trigger')
    assert load_capture_mode(path) == 'trigger'
    assert json.loads(path.read_text()) == {'last_recipe': 'test2', 'other_setting': 42, 'capture_mode': 'trigger'}
    save_capture_mode(path, 'master')
    assert load_capture_mode(path) == 'master'


def test_corrupt_settings_are_preserved_and_invalid_modes_are_not_saved(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{broken')
    assert load_capture_mode(path) is None
    save_capture_mode(path, 'trigger')
    assert path.read_text() == '{broken'
    path.unlink()
    save_capture_mode(path, 'invalid')
    assert not path.exists()

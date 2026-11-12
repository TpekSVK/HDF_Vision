import json
import sqlite3

import pytest

from app.models.schema import RecipeV2, RecipeView
from app.services.db_service import DbService
from app.services.inspection_groups import GroupOutcome
from app.services.modbus_service import ModbusConfig
from app.services.results_history import query_history, history_cursor, export_history
from app.services.storage_service import load_recipe_config, save_recipe_config


def test_v3_upgrade_is_explicit_and_preserves_exact_backup(tmp_path):
    document = RecipeV2().to_dict()
    document['format_version'] = 3
    for view in document['views']:
        for field in ('camera_id', 'golden_camera', 'output_group', 'output_mode', 'and_timeout_ms'):
            view.pop(field, None)
    directory = tmp_path / 'recipes' / 'legacy'
    directory.mkdir(parents=True)
    path = directory / 'recipe.json'
    original = json.dumps(document, ensure_ascii=False, indent=4).encode()
    path.write_bytes(original)
    recipe = load_recipe_config('legacy', base_dir=tmp_path)
    assert recipe.views[0].camera_id == 'camera_1'
    assert path.read_bytes() == original  # reading alone never rewrites data
    save_recipe_config('legacy', recipe, base_dir=tmp_path)
    assert json.loads(path.read_text())['format_version'] == 4
    assert (directory / 'recipe.pre-dual-v3.json').read_bytes() == original
    save_recipe_config('legacy', recipe, base_dir=tmp_path)
    assert (directory / 'recipe.pre-dual-v3.json').read_bytes() == original


def test_shared_database_camera_filters_and_pagination_never_duplicate_images(tmp_path):
    path = tmp_path / 'results.db'
    db = DbService(path)
    ident = db.ensure_recipe('Forma')
    for camera in ('camera_1', 'camera_2'):
        for index in range(4):
            db._conn.execute('''INSERT INTO results
                (ts_ms, recipe_id, ok, view_id, run_id, meta_json) VALUES (?,?,?,?,?,?)''',
                (1000 + index, ident, 1, 'view_' + camera[-1], 'cycle', json.dumps({'camera_id': camera})))
    db._conn.commit()
    outcome = GroupOutcome('group_1', 'cycle', 'nok', 'Vypršal limit', {'view_1': 'ok'}, ('view_2',), 0, 1)
    db.insert_group_result('Forma', outcome)
    db.close()
    sources = [dict(id=camera, camera_id=camera, name=camera, db_path=path) for camera in ('camera_1', 'camera_2')]
    filters = dict(start=0, end=10000, recipe='', view='', ok=None)
    rows = query_history(sources, filters, limit=100)
    assert len(rows) == 8 and len({row['id'] for row in rows}) == 8
    assert all(row['group_status'] == 'nok' and row['group_reason'] == 'Vypršal limit' for row in rows)
    assert all(json.loads(row['meta_json'])['camera_id'] == 'camera_2'
               for row in query_history(sources[1:], filters, limit=100))
    paged, cursor = [], None
    while True:
        page = query_history(sources, filters, cursor, 3)
        if not page: break
        paged.extend(page); cursor = history_cursor(page[-1])
    assert [row['id'] for row in paged] == [row['id'] for row in rows]
    assert export_history(sources, filters, tmp_path / 'export.csv') == 8


@pytest.mark.parametrize('damage', ['foreign', 'duplicate', 'reserved'])
def test_group_mapping_cannot_write_another_group_or_reserved_relays(damage):
    cfg = ModbusConfig(output_groups={'group_1': {'ok': 0, 'nok': 1, 'heartbeat': 2}})
    if damage == 'foreign': cfg.output_groups['group_1']['ok'] = 3
    if damage == 'duplicate': cfg.output_groups['group_1']['nok'] = 0
    if damage == 'reserved': cfg.output_groups['group_1']['heartbeat'] = 7
    with pytest.raises(ValueError): cfg.group_config('group_1')


def test_device_upgrade_keeps_v1_bytes_and_legacy_save_has_no_new_keys(tmp_path):
    from app.services.workstation_devices import WorkstationDevices, StationBinding
    path = tmp_path/'devices.json'
    service = WorkstationDevices(path)
    service.save([StationBinding('camera_1','A','USB-A','PICO')])
    original=path.read_bytes()
    assert 'pico_channel' not in json.loads(original)['stations'][0]
    upgraded=[StationBinding('camera_1','A','USB-A','PICO',1), StationBinding('camera_2','B','USB-B','PICO',2)]
    service.save(upgraded)
    assert (tmp_path/'devices.pre-dual-v1.json').read_bytes()==original
    assert service.load()==upgraded

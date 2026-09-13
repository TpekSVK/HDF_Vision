from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import numpy as np
import pytest
from app.services.inspection_controller import InspectionController, InspectionState
from app.services.frame_coordinates import InspectionFrame, reused_view_frame
from app.services.storage_service import save_golden, save_production_result
from test_inspection_runtime import runtime_fixture


def test_three_independent_cycles_one_camera_fails_other_two_complete(tmp_path, monkeypatch):
    stations = []
    barrier = threading.Barrier(3)
    for i in range(3):
        runtime, _, options, _, outputs, config = runtime_fixture(tmp_path / str(i), monkeypatch)
        config.views[0].external_source = 'pico'
        runtime.camera_id = f'camera_{i + 1}'; runtime.pico_id = f'pico_{i + 1}'
        runtime.data_root = tmp_path / str(i)
        controller = InspectionController(runtime.camera_id)
        controller.prepare(lambda: None, lambda: None)
        image = np.arange(16 * 20, dtype=np.uint8).reshape(16, 20)
        def capture(*, index=i, frame=image, **kwargs):
            barrier.wait(timeout=3)
            if index == 1: raise RuntimeError('Camera 2 disconnected')
            return frame.copy()
        runtime._capture_frame_for_view = capture
        stations.append((runtime, controller, options, outputs))
    def execute(station):
        runtime, controller, options, _ = station
        request, reason = controller.admit('pico')
        try:
            result = runtime.run(controller, request, options)
        except Exception as exc:
            controller.finish(request, error=exc)
            return exc
        controller.finish(request)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(execute, stations))
    assert not isinstance(results[0], Exception)
    assert isinstance(results[1], RuntimeError)
    assert not isinstance(results[2], Exception)
    assert [s[1].state for s in stations] == [InspectionState.READY, InspectionState.ERROR, InspectionState.READY]
    assert 'ok' not in stations[1][3]
    assert stations[0][0].records[0]['camera_id'] == 'camera_1'
    assert stations[2][0].records[0]['camera_id'] == 'camera_3'
    stations[0][1].pause(lambda: None)
    assert stations[2][1].admit('pico')[0] is not None


def test_cross_camera_frame_and_request_are_rejected(tmp_path, monkeypatch):
    capture = InspectionFrame(np.zeros((2, 2), np.uint8), 0, 'camera_2')
    with pytest.raises(ValueError, match='inej kamere'):
        reused_view_frame({'injected_capture': capture}, {}, 0, camera_id='camera_1')
    runtime, _, options, _, _, _ = runtime_fixture(tmp_path, monkeypatch)
    wrong = InspectionController('camera_2'); wrong.prepare(lambda: None, lambda: None)
    request, _ = wrong.admit('pico')
    with pytest.raises(ValueError, match='inej kamerovej'):
        runtime.run(wrong, request, options)


def test_same_recipe_and_run_names_store_in_separate_station_roots(tmp_path):
    import imageio.v3 as iio
    images = [np.zeros((8, 8), np.uint8), np.full((8, 8), 180, np.uint8)]
    roots = [tmp_path, tmp_path / 'stations/camera_2']
    for image, root in zip(images, roots):
        save_golden(image, 'default', base_dir=root)
    for image, root in zip(images, roots):
        np.testing.assert_array_equal(iio.imread(root / 'recipes/default/golden.png'), image)
    artifacts = [save_production_result(image, {'camera_id': str(i)}, 'default', False, False,
                 run_id='same', view_id='view_1', base_dir=root)
                 for i, (image, root) in enumerate(zip(images, roots))]
    assert artifacts[0]['meta'] != artifacts[1]['meta']
    assert str(roots[1] / 'runs') in artifacts[1]['meta']

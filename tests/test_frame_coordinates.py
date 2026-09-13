import numpy as np
import pytest
from app.services.frame_coordinates import InspectionFrame, reused_view_frame


@pytest.mark.parametrize('source_rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('target_rotation', [0, 90, 180, 270])
def test_reused_capture_matches_direct_camera_rotation(source_rotation, target_rotation):
    raw = np.arange(5 * 9, dtype=np.uint8).reshape(5, 9)
    source = np.rot90(raw, k=-source_rotation // 90).copy()
    capture = InspectionFrame(source, source_rotation)
    result = reused_view_frame({'frame_source_view_id': 'source'}, {'source': capture}, target_rotation)
    np.testing.assert_array_equal(result, np.rot90(raw, k=-target_rotation // 90))
    result[:] = 0
    np.testing.assert_array_equal(source, np.rot90(raw, k=-source_rotation // 90))


def test_branch_chain_does_not_accumulate_rotations():
    raw = np.arange(7 * 11 * 3, dtype=np.uint8).reshape(7, 11, 3)
    capture = InspectionFrame(raw.copy(), 0)
    for rotation in (180, 90, 270, 180, 0):
        result = reused_view_frame({'injected_capture': capture}, {}, rotation)
        np.testing.assert_array_equal(result, np.rot90(raw, k=-rotation // 90))
        capture = InspectionFrame(result, rotation)


def test_missing_current_cycle_capture_is_not_a_camera_fallback():
    with pytest.raises(ValueError, match='aktuálnej kontrole'):
        reused_view_frame({'frame_source_view_id': 'source'}, {}, 180)
    assert reused_view_frame({}, {}, 180) is None


def test_unannotated_capture_cannot_enter_another_view():
    with pytest.raises(TypeError):
        reused_view_frame({'injected_capture': np.zeros((4, 5))}, {}, 0)

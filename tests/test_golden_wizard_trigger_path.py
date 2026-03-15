from pathlib import Path


def _extract_trigger_mode_block(source: str) -> str:
    marker = 'if runtime_capture_mode == "trigger":'
    start = source.index(marker)
    rest = source[start:]
    end = rest.index("else:")
    return rest[:end]


def test_golden_trigger_capture_uses_runtime_callback_path() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    trigger_block = _extract_trigger_mode_block(source)

    assert "self._capture_frame_for_golden" in trigger_block
    assert "trigger_gap_ms=float(trigger_gap_ms)" in trigger_block


def test_golden_capture_mode_source_is_runtime_not_stream_mode() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")

    assert "self._get_capture_mode" in source
    assert "if stream_mode == 1:" not in source


def test_golden_trigger_mode_has_no_preview_fallbacks() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    trigger_block = _extract_trigger_mode_block(source)

    assert "last_frame(" not in trigger_block
    assert "one_shot(" not in trigger_block
    assert "last_frame_u8" not in trigger_block


def test_golden_capture_prepares_camera_from_active_view_before_mode_resolution() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")

    prepare_call = "self._prepare_camera_for_golden_capture(view_id=self._active_view_id)"
    mode_marker = "runtime_capture_mode = \"master\""

    assert prepare_call in source
    assert source.index(prepare_call) < source.index(mode_marker)


def test_main_window_wires_shared_prepare_callback_to_wizard() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "prepare_camera_for_golden_capture=self.prepare_camera_for_golden_capture" in source
    assert "def prepare_camera_for_golden_capture" in source
    assert "def prepare_camera_for_view_capture" in source

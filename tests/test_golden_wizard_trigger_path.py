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


def test_golden_capture_passes_active_view_settle_ms() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert 'active_view = self._resolve_active_capture_view(requested_view_id=view_id)' in source
    assert 'settle_ms = getattr(active_view, "settle_ms", None) if active_view is not None else None' in source
    assert 'settle_ms=settle_ms,' in source

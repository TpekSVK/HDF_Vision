from pathlib import Path


def _extract_trigger_mode_block(source: str) -> str:
    marker = "if stream_mode == 1:"
    start = source.index(marker)
    rest = source[start:]
    end = rest.index("else:")
    return rest[:end]


def test_golden_trigger_capture_uses_capture_trigger_frame_with_trigger_fn() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    trigger_block = _extract_trigger_mode_block(source)

    assert "capture_trigger_frame(" in trigger_block
    assert "trigger_fn=self._trigger_fn" in trigger_block
    assert "trigger_gap_ms=float(trigger_gap_ms)" in trigger_block


def test_golden_trigger_mode_has_no_preview_fallbacks() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    trigger_block = _extract_trigger_mode_block(source)

    assert "last_frame(" not in trigger_block
    assert "one_shot(" not in trigger_block
    assert "last_frame_u8" not in trigger_block

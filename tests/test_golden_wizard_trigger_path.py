import ast
from pathlib import Path


def test_golden_capture_uses_shared_runtime_callback_path() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")

    assert "self._capture_frame_for_golden" in source
    assert 'capture_request_source="golden_wizard"' in source


def test_golden_capture_mode_source_is_runtime_not_stream_mode() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")

    assert "self._get_capture_mode" in source
    assert "if stream_mode == 1:" not in source


def test_tool_test_uses_shared_capture_callback_no_one_shot_fallback() -> None:
    source = Path("app/ui/golden_wizard/golden_wizard.py").read_text(encoding="utf-8")
    start = source.index("def _on_tool_test_requested")
    end = source.index("def _edit_tool", start)
    block = source[start:end]

    assert 'capture_request_source="tool_test"' in block
    assert "self.cam.one_shot()" not in block
    assert "last_frame_u8" not in block


def test_golden_capture_passes_active_view_settle_ms() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert 'active_view = self._resolve_active_capture_view(requested_view_id=view_id)' in source
    assert 'settle_ms = getattr(active_view, "settle_ms", None) if active_view is not None else None' in source
    assert "capture_request_source=capture_request_source," in source


def test_external_source_change_clears_input_namespace() -> None:
    source = Path("app/ui/golden_wizard/view_config_dialog.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    dialog = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ViewConfigDialog")
    method = next(
        node for node in dialog.body
        if isinstance(node, ast.FunctionDef) and node.name == "_on_external_source_changed"
    )
    harness = ast.ClassDef("Harness", [], [], [method], [])
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module([harness], [])), source, "exec"), namespace)

    class Combo:
        def __init__(self):
            self.indexes = []

        def currentData(self):
            return "modbus"

        def setCurrentIndex(self, index):
            self.indexes.append(index)

    instance = namespace["Harness"]()
    instance._last_external_source = "pico"
    instance._external_source_combo = Combo()
    instance._external_input_combo = Combo()
    calls = []
    instance._populate_external_inputs = lambda **kwargs: calls.append(kwargs)
    instance._on_external_source_changed()

    assert calls == [{"preserve_selection": False}]
    assert instance._external_input_combo.indexes == [-1]

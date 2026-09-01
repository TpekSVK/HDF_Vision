import ast
import logging
from numbers import Integral
from pathlib import Path
from types import SimpleNamespace

from app.utils.external_source import format_external_input, normalize_external_source


def _load_harness():
    tree = ast.parse(Path("app/ui/main_window.py").read_text(encoding="utf-8"))
    main_window = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    names = {"_resolve_external_trigger_view", "_reset_external_sequence_state"}
    methods = [
        node for node in main_window.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    harness = ast.ClassDef("RoutingHarness", [], [], methods, [])
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    namespace = {
        "Any": object,
        "Integral": Integral,
        "format_external_input": format_external_input,
        "normalize_external_source": normalize_external_source,
    }
    exec(compile(module, "app/ui/main_window.py", "exec"), namespace)
    return namespace["RoutingHarness"]


RoutingHarness = _load_harness()


def _window(*, enabled=(1, 3, 4, 5), mode="RUN"):
    window = RoutingHarness()
    window.mode = mode
    window._logger = logging.getLogger("test.external.routing")
    window.pico_config = SimpleNamespace(is_input_enabled=lambda value: value in enabled)
    window._reset_external_sequence_state()
    return window


def _spec(name, source, mode="sequential", input_index=None, trigger_mode="external"):
    return {
        "view": SimpleNamespace(id=name.lower(), name=name),
        "trigger_mode": trigger_mode,
        "external_trigger_mode": mode,
        "external_source": source,
        "external_request_input": input_index,
    }


def _name(spec):
    return None if spec is None else spec["view"].name


def test_explicit_pico_selection_and_source_namespace():
    specs = [
        _spec("Pico1", "pico", "explicit", 1),
        _spec("Pico5", "pico", "explicit", 5),
        _spec("Modbus1", "modbus", "explicit", 1),
    ]
    window = _window()

    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=5)) == "Pico5"
    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1)) == "Pico1"
    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="modbus", input_index=1)) == "Modbus1"


def test_disabled_pico_input_is_ignored_without_advancing_sequence():
    specs = [_spec("View1", "pico"), _spec("View2", "pico")]
    window = _window(enabled=(1,))

    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1)) == "View1"
    assert window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=5) is None
    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1)) == "View2"


def test_sequential_pico_wraps_and_excludes_other_view_types():
    specs = [
        _spec("View1", "pico"),
        _spec("Timer", "pico", trigger_mode="timed"),
        _spec("View2", "pico"),
        _spec("Explicit", "pico", "explicit", 3),
        _spec("View3", "pico"),
    ]
    window = _window()

    names = [
        _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=value))
        for value in (1, 4, 1, 4)
    ]
    assert names == ["View1", "View2", "View3", "View1"]


def test_source_sequences_are_independent():
    specs = [
        _spec("Pico1", "pico"), _spec("Pico2", "pico"),
        _spec("Modbus1", "modbus"), _spec("Modbus2", "modbus"),
    ]
    window = _window(enabled=(1,))

    events = [("pico", 1), ("modbus", 1), ("pico", 1), ("modbus", 1)]
    assert [
        _name(window._resolve_external_trigger_view(view_specs=specs, source=source, input_index=index))
        for source, index in events
    ] == ["Pico1", "Modbus1", "Pico2", "Modbus2"]


def test_explicit_match_has_priority_and_does_not_advance_sequence():
    specs = [
        _spec("Sequential1", "pico"), _spec("Sequential2", "pico"),
        _spec("Explicit3", "pico", "explicit", 3),
    ]
    window = _window()

    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=3)) == "Explicit3"
    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1)) == "Sequential1"


def test_duplicate_explicit_match_and_no_match_are_ignored():
    duplicate = [_spec("A", "pico", "explicit", 3), _spec("B", "pico", "explicit", 3)]
    window = _window()
    assert window._resolve_external_trigger_view(view_specs=duplicate, source="pico", input_index=3) is None
    assert window._resolve_external_trigger_view(view_specs=[], source="pico", input_index=1) is None


def test_setup_events_do_not_select_or_advance_and_recipe_reset_starts_first():
    specs = [_spec("First", "pico"), _spec("Second", "pico")]
    window = _window(mode="SETUP", enabled=(1,))
    assert window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1) is None
    assert window._external_sequence_index["pico"] == 0

    window.mode = "RUN"
    assert _name(window._resolve_external_trigger_view(view_specs=specs, source="pico", input_index=1)) == "First"
    window._reset_external_sequence_state()  # recipe activation
    replacement = [_spec("RecipeB1", "pico"), _spec("RecipeB2", "pico")]
    assert _name(window._resolve_external_trigger_view(view_specs=replacement, source="pico", input_index=1)) == "RecipeB1"

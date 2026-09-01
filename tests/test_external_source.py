from app.utils.external_source import (
    format_external_input,
    normalize_external_input,
    normalize_external_source,
)


def test_source_normalization_is_canonical_and_safe():
    for value in ("pico", "Pico", "PICO", "Pico USB"):
        assert normalize_external_source(value) == "pico"
    for value in ("modbus", "Modbus", "MODBUS"):
        assert normalize_external_source(value) == "modbus"
    assert normalize_external_source("abc") is None
    assert normalize_external_source(None) is None


def test_input_normalization_is_source_aware_and_bounded():
    assert normalize_external_input("pico", "IN1") == 1
    assert normalize_external_input("Pico USB", "IN8") == 8
    assert normalize_external_input("modbus", "DI1") == 1
    assert normalize_external_input("MODBUS", "DI8") == 8
    for source, value in (
        ("pico", "IN0"), ("pico", "IN9"),
        ("modbus", "DI0"), ("modbus", "DI9"),
        ("pico", "DI3"), ("modbus", "IN3"),
    ):
        assert normalize_external_input(source, value) is None


def test_display_helper_includes_source_namespace():
    assert format_external_input("pico", 3) == "Pico IN3"
    assert format_external_input("modbus", 3) == "Modbus DI3"


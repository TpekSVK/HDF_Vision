"""Canonical values and small helpers for recipe external trigger inputs."""

from __future__ import annotations

import re
from typing import Any

EXTERNAL_SOURCE_PICO = "pico"
EXTERNAL_SOURCE_MODBUS = "modbus"
EXTERNAL_SOURCES = (EXTERNAL_SOURCE_PICO, EXTERNAL_SOURCE_MODBUS)


def normalize_external_source(value: Any) -> str | None:
    """Return the canonical external source, or ``None`` for unknown data."""
    text = str(value or "").strip().lower().replace("_", " ")
    if text in {"pico", "pico usb"}:
        return EXTERNAL_SOURCE_PICO
    if text == "modbus":
        return EXTERNAL_SOURCE_MODBUS
    return None


def normalize_external_input(source: Any, value: Any) -> int | None:
    """Normalize IN1/DI1 or an integer to the shared, one-based 1..8 index."""
    normalized_source = normalize_external_source(source)
    if normalized_source is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().upper()
        match = re.fullmatch(r"(IN|DI)?\s*([0-9]+)", text)
        if match is None:
            return None
        prefix, number = match.groups()
        expected = "IN" if normalized_source == EXTERNAL_SOURCE_PICO else "DI"
        if prefix and prefix != expected:
            return None
        value = number
    try:
        index = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return index if 1 <= index <= 8 else None


def format_external_input(source: Any, input_index: Any) -> str:
    """Format a canonical source/input pair for UI messages and logs."""
    normalized_source = normalize_external_source(source)
    index = normalize_external_input(normalized_source, input_index)
    if normalized_source == EXTERNAL_SOURCE_PICO:
        return f"Pico IN{index}" if index is not None else "Pico"
    if normalized_source == EXTERNAL_SOURCE_MODBUS:
        return f"Modbus DI{index}" if index is not None else "Modbus"
    return "Neznámy externý vstup"

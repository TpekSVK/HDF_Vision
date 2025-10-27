"""Shared UI helpers for Golden Wizard configuration forms."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QWidget

from app.services.golden_wizard_logic import _SUPPORTED_FORM_FIELD_TYPES, _format_number


def _format_spec_tooltip(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    description = (spec.get("description") or "").strip()
    if description:
        parts.append(description)

    min_val = spec.get("min")
    max_val = spec.get("max")
    if min_val is not None or max_val is not None:
        if min_val is not None and max_val is not None:
            parts.append(
                f"Valid range: {_format_number(min_val)} – {_format_number(max_val)}"
            )
        elif min_val is not None:
            parts.append(f"Minimum: {_format_number(min_val)}")
        elif max_val is not None:
            parts.append(f"Maximum: {_format_number(max_val)}")

    step = spec.get("step")
    if step not in (None, 0):
        parts.append(f"Step: {_format_number(step)}")

    if "default" in spec and spec.get("default") is not None:
        parts.append(f"Default: {_format_number(spec.get('default'))}")

    return "\n".join(parts)


def _create_form_widget(spec: dict[str, Any], parent: QWidget) -> QWidget | None:
    field_type = (spec.get("type") or "").lower()
    if field_type == "bool":
        checkbox = QCheckBox(parent)
        checkbox.setTristate(False)
        default = spec.get("default")
        if default is not None:
            checkbox.setChecked(bool(default))
        return checkbox
    if field_type == "enum":
        combo = QComboBox(parent)
        for value, label in spec.get("choices", []) or []:
            combo.addItem(str(label), value)
        default = spec.get("default")
        if default is not None and combo.count():
            index = combo.findData(default)
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo if combo.count() else None
    if field_type == "int":
        spin = QSpinBox(parent)
        spin.setKeyboardTracking(False)
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is None:
            min_val = -10_000_000
        if max_val is None:
            max_val = 10_000_000
        spin.setRange(int(min_val), int(max_val))
        step = spec.get("step")
        if step is not None:
            try:
                spin.setSingleStep(max(1, int(step)))
            except Exception:  # pragma: no cover - defensive fallback
                pass
        default = spec.get("default")
        if default is not None:
            try:
                spin.setValue(int(round(float(default))))
            except Exception:  # pragma: no cover - defensive fallback
                spin.setValue(int(min_val))
        return spin
    if field_type == "float":
        spin = QDoubleSpinBox(parent)
        spin.setKeyboardTracking(False)
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is None:
            min_val = -1e9
        if max_val is None:
            max_val = 1e9
        spin.setRange(float(min_val), float(max_val))
        precision = spec.get("precision")
        if precision is None:
            precision = spec.get("decimals", 4)
        try:
            decimals = max(0, int(precision))
        except Exception:  # pragma: no cover - defensive fallback
            decimals = 4
        spin.setDecimals(decimals)
        step = spec.get("step")
        if step is not None:
            try:
                spin.setSingleStep(float(step))
            except Exception:  # pragma: no cover - defensive fallback
                pass
        default = spec.get("default")
        if default is not None:
            try:
                spin.setValue(float(default))
            except Exception:  # pragma: no cover - defensive fallback
                spin.setValue(float(min_val))
        return spin
    return None


def _set_form_widget_value(widget: QWidget, spec: dict[str, Any], value: Any) -> None:
    field_type = (spec.get("type") or "").lower()
    if value is None:
        value = spec.get("default")
    if field_type == "bool" and isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
        return
    if field_type == "enum" and isinstance(widget, QComboBox):
        if widget.count() == 0:
            return
        index = widget.findData(value)
        if index < 0 and spec.get("default") is not None:
            index = widget.findData(spec.get("default"))
        if index < 0:
            index = 0
        widget.setCurrentIndex(max(0, index))
        return
    if field_type == "int" and isinstance(widget, QSpinBox):
        fallback = spec.get("default")
        if fallback is None:
            fallback = widget.minimum()
        try:
            widget.setValue(int(round(float(value))))
        except Exception:  # pragma: no cover - defensive fallback
            widget.setValue(int(round(float(fallback))))
        return
    if field_type == "float" and isinstance(widget, QDoubleSpinBox):
        fallback = spec.get("default")
        if fallback is None:
            fallback = widget.minimum()
        try:
            widget.setValue(float(value))
        except Exception:  # pragma: no cover - defensive fallback
            widget.setValue(float(fallback))


def _get_form_widget_value(widget: QWidget, spec: dict[str, Any]) -> Any:
    field_type = (spec.get("type") or "").lower()
    if field_type == "bool" and isinstance(widget, QCheckBox):
        return bool(widget.isChecked())
    if field_type == "enum" and isinstance(widget, QComboBox):
        if widget.count() == 0:
            return None
        return widget.currentData()
    if field_type == "int" and isinstance(widget, QSpinBox):
        return int(widget.value())
    if field_type == "float" and isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    return None


__all__ = [
    "_SUPPORTED_FORM_FIELD_TYPES",
    "_format_spec_tooltip",
    "_create_form_widget",
    "_set_form_widget_value",
    "_get_form_widget_value",
]

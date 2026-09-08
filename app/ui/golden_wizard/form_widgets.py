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
    display_scale = float(spec.get("display_scale", 1.0) or 1.0)
    suffix = str(spec.get("suffix", "") or "")
    display_min = float(min_val) * display_scale if min_val is not None else None
    display_max = float(max_val) * display_scale if max_val is not None else None
    if min_val is not None or max_val is not None:
        if min_val is not None and max_val is not None:
            parts.append(
                f"Rozsah: {_format_number(display_min)} – {_format_number(display_max)}{suffix}"
            )
        elif min_val is not None:
            parts.append(f"Minimum: {_format_number(display_min)}{suffix}")
        elif max_val is not None:
            parts.append(f"Maximum: {_format_number(display_max)}{suffix}")

    step = spec.get("step")
    if step not in (None, 0):
        parts.append(f"Krok: {_format_number(float(step) * display_scale)}{suffix}")

    if "default" in spec and spec.get("default") is not None:
        default_value = spec.get("default")
        try:
            default_value = float(default_value) * display_scale
        except (TypeError, ValueError):
            pass
        parts.append(
            f"Predvolené: {_format_number(default_value)}{suffix}"
        )

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
        suffix = str(spec.get("suffix", "") or "")
        if suffix:
            spin.setSuffix(suffix)
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
        display_scale = float(spec.get("display_scale", 1.0) or 1.0)
        spin.setRange(float(min_val) * display_scale, float(max_val) * display_scale)
        precision = spec.get("precision")
        if precision is None:
            precision = spec.get("decimals", 4)
        precision = spec.get("display_decimals", precision)
        try:
            decimals = max(0, int(precision))
        except Exception:  # pragma: no cover - defensive fallback
            decimals = 4
        spin.setDecimals(decimals)
        step = spec.get("step")
        if step is not None:
            try:
                spin.setSingleStep(float(step) * display_scale)
            except Exception:  # pragma: no cover - defensive fallback
                pass
        default = spec.get("default")
        if default is not None:
            try:
                spin.setValue(float(default) * display_scale)
            except Exception:  # pragma: no cover - defensive fallback
                spin.setValue(float(min_val) * display_scale)
        suffix = str(spec.get("suffix", "") or "")
        if suffix:
            spin.setSuffix(suffix)
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
            scale = float(spec.get("display_scale", 1.0) or 1.0)
            widget.setValue(float(value) * scale)
        except Exception:  # pragma: no cover - defensive fallback
            scale = float(spec.get("display_scale", 1.0) or 1.0)
            widget.setValue(float(fallback) * scale)


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
        scale = float(spec.get("display_scale", 1.0) or 1.0)
        return float(widget.value()) / scale
    return None


__all__ = [
    "_SUPPORTED_FORM_FIELD_TYPES",
    "_format_spec_tooltip",
    "_create_form_widget",
    "_set_form_widget_value",
    "_get_form_widget_value",
]

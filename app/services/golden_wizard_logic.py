"""Non-UI helpers shared across Golden Wizard dialogs."""

from __future__ import annotations

import math
from typing import Any, Optional, Dict, Tuple

_SUPPORTED_FORM_FIELD_TYPES = {"int", "float", "bool", "enum"}


def _coerce_bool_value(value: Any) -> tuple[Optional[bool], Optional[str]]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        return bool(value), None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True, None
        if text in {"0", "false", "no", "n", "off"}:
            return False, None
        return None, "Value must be 'true' or 'false'."
    return None, "Invalid boolean value."


def _coerce_numeric_value(value: Any, *, number_type: str) -> tuple[Optional[float], Optional[str]]:
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "Boolean value is not allowed."
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None, None
        try:
            if number_type == "int":
                return float(int(text, 10)), None
            return float(text)
        except (TypeError, ValueError):
            return None, "Value must be a number."
    return None, "Value must be a number."


def _apply_numeric_constraints(value: float, spec: dict[str, Any], *, number_type: str) -> tuple[Any, list[str]]:
    errors: list[str] = []
    try:
        if math.isnan(value) or math.isinf(value):
            return None, ["Value must be a finite number."]
    except TypeError:
        return None, ["Value must be a number."]

    min_val = spec.get("min")
    max_val = spec.get("max")

    if min_val is not None and value < float(min_val):
        errors.append(f"Value must be ≥ {min_val}.")
    if max_val is not None and value > float(max_val):
        errors.append(f"Value must be ≤ {max_val}.")

    clamped = value
    if min_val is not None:
        clamped = max(clamped, float(min_val))
    if max_val is not None:
        clamped = min(clamped, float(max_val))

    if number_type == "int":
        return int(round(clamped)), errors

    precision = spec.get("precision")
    if precision is None:
        precision = spec.get("decimals")
    if isinstance(precision, int) and precision >= 0:
        clamped = round(clamped, precision)

    return float(clamped), errors


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _normalize_field_value(value: Any, spec: dict[str, Any]) -> tuple[Any, list[str]]:
    errors: list[str] = []
    required = bool(spec.get("required"))
    field_type = (spec.get("type") or "").lower()

    if value is None or value == "":
        if required:
            errors.append("This field is required.")
            return None, errors
        default = spec.get("default")
        return default, errors

    if field_type == "bool":
        coerced, err = _coerce_bool_value(value)
        if err:
            errors.append(err)
            return None, errors
        return coerced, errors

    if field_type == "enum":
        valid_choices = {choice[0] for choice in spec.get("choices", []) or []}
        if value not in valid_choices:
            errors.append("Select one of the available options.")
            return None, errors
        return value, errors

    if field_type in {"int", "float"}:
        coerced, err = _coerce_numeric_value(value, number_type=field_type)
        if err:
            errors.append(err)
            return None, errors
        if coerced is None:
            if required:
                errors.append("This field is required.")
            return None, errors
        normalized, range_errors = _apply_numeric_constraints(
            coerced, spec, number_type=field_type
        )
        errors.extend(range_errors)
        return normalized, errors

    return value, errors


def _validate_values_against_specs(
    values: dict[str, Any], specs: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    normalized = dict(values or {})
    errors: dict[str, list[str]] = {}

    for name, spec in (specs or {}).items():
        raw_value = values.get(name)
        normalized_value, field_errors = _normalize_field_value(raw_value, spec)
        if field_errors:
            errors[name] = field_errors
        if normalized_value is None:
            normalized.pop(name, None)
        else:
            normalized[name] = normalized_value

    return normalized, errors


def _validate_params_and_thresholds(
    params: dict[str, Any],
    thresholds: dict[str, Any],
    param_specs: dict[str, dict[str, Any]],
    threshold_specs: dict[str, dict[str, Any]],
) -> tuple[bool, dict[str, Dict[str, list[str]]], dict[str, dict[str, Any]]]:
    normalized_params, param_errors = _validate_values_against_specs(params, param_specs)
    normalized_thresholds, threshold_errors = _validate_values_against_specs(
        thresholds, threshold_specs
    )

    ok = not param_errors and not threshold_errors
    errors = {"params": param_errors, "thresholds": threshold_errors}
    normalized = {"params": normalized_params, "thresholds": normalized_thresholds}
    return ok, errors, normalized


__all__ = [
    "_SUPPORTED_FORM_FIELD_TYPES",
    "_coerce_bool_value",
    "_coerce_numeric_value",
    "_apply_numeric_constraints",
    "_format_number",
    "_normalize_field_value",
    "_validate_values_against_specs",
    "_validate_params_and_thresholds",
]

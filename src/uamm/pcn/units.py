from __future__ import annotations


def validate_numeric_unit(value: float | int | str, unit: str) -> bool:
    """Validate that a numeric value is compatible with a given unit.

    Uses pint if available; otherwise returns True for known simple units as a
    conservative fallback (no conversion performed).
    """
    try:
        import pint  # type: ignore
    except Exception:
        simple = {"%", "percent", "ms", "s", "m", "kg", "g", "count"}
        return str(unit).strip().lower() in simple
    try:
        ureg = pint.UnitRegistry()
        q = float(value) * ureg.parse_expression(str(unit))
        _ = q.to_base_units()
        return True
    except Exception:
        return False


__all__ = ["validate_numeric_unit"]

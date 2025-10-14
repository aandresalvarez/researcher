from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _as_float(x: Any) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _column_values(rows: Iterable[Dict[str, Any]], col: str) -> List[Any]:
    out: List[Any] = []
    for r in rows:
        out.append(r.get(col))
    return out


def evaluate_checks(
    rows: List[Dict[str, Any]], checks: Dict[str, Dict[str, Any]]
) -> List[str]:
    """Evaluate simple column checks on a list of row dicts.

    checks: { column: { 'non_negative': bool, 'min': num, 'max': num, 'monotonic': 'increasing'|'decreasing'|'nondecreasing'|'nonincreasing' } }
    Returns list of violation codes like 'col:id:min', 'col:cnt:nonnegative', 'col:id:monotonic'.
    """
    violations: List[str] = []
    if not rows or not checks:
        return violations
    for col, spec in checks.items():
        vals = _column_values(rows, col)
        # numeric conversions where applicable
        nums = [_as_float(v) for v in vals]
        if spec.get("non_negative"):
            for i, v in enumerate(nums):
                if v is None:
                    continue
                if v < 0:
                    violations.append(f"col:{col}:nonnegative")
                    break
        if spec.get("min") is not None:
            try:
                m = float(spec.get("min"))
                for v in nums:
                    if v is None:
                        continue
                    if v < m:
                        violations.append(f"col:{col}:min")
                        break
            except Exception:
                pass
        if spec.get("max") is not None:
            try:
                m = float(spec.get("max"))
                for v in nums:
                    if v is None:
                        continue
                    if v > m:
                        violations.append(f"col:{col}:max")
                        break
            except Exception:
                pass
        mono = spec.get("monotonic")
        if mono:
            prev: float | None = None
            ok = True
            for v in nums:
                if v is None:
                    continue
                if prev is None:
                    prev = v
                    continue
                if mono == "increasing" and not (v > prev):
                    ok = False
                    break
                if mono == "decreasing" and not (v < prev):
                    ok = False
                    break
                if mono == "nondecreasing" and not (v >= prev):
                    ok = False
                    break
                if mono == "nonincreasing" and not (v <= prev):
                    ok = False
                    break
                prev = v
            if not ok:
                violations.append(f"col:{col}:monotonic")
    return violations


__all__ = ["evaluate_checks"]

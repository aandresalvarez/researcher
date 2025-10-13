from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import sqlite3


@dataclass
class QuantileDrift:
    deltas: Dict[str, float]
    max_abs_delta: float
    sample_size: int


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def recent_scores(
    db_path: str,
    domain: str,
    *,
    limit: int = 200,
) -> list[float]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT S FROM cp_artifacts WHERE domain=? ORDER BY ts DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
        return [float(r["S"]) for r in rows]
    finally:
        conn.close()


def compute_quantile_drift(
    baseline: Dict[str, float],
    observed: Dict[str, float],
    *,
    sample_size: Optional[int] = None,
) -> QuantileDrift:
    deltas: Dict[str, float] = {}
    max_delta = 0.0
    for key, base_val in baseline.items():
        obs_val = observed.get(key)
        if obs_val is None:
            continue
        delta = float(obs_val) - float(base_val)
        deltas[key] = delta
        max_delta = max(max_delta, abs(delta))
    samples = sample_size if sample_size is not None else len(observed)
    return QuantileDrift(deltas=deltas, max_abs_delta=max_delta, sample_size=samples)


def needs_attention(
    drift: QuantileDrift,
    *,
    tolerance: float,
    min_sample_size: int,
) -> bool:
    if drift.sample_size < max(1, min_sample_size):
        return False
    return drift.max_abs_delta > tolerance


def rolling_false_accept_rate(
    cp_stats: Dict[str, Dict[str, float]],
    target: float,
    tolerance: float,
) -> Dict[str, Dict[str, float]]:
    alerts: Dict[str, Dict[str, float]] = {}
    for domain, stats in cp_stats.items():
        rate = float(stats.get("rate_false_accept", 0.0) or 0.0)
        if rate > target + tolerance:
            alerts[domain] = {
                "false_accept_rate": rate,
                "target": target,
                "tolerance": tolerance,
            }
    return alerts

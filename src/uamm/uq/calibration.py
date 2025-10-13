import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .snne import normalize as logistic_normalize


QuantileSeries = List[Tuple[float, float]]


@dataclass
class _CacheEntry:
    quantiles: QuantileSeries
    ts: float


class SNNECalibrator:
    """Per-domain SNNE calibrator that maps raw scores to [0,1] via stored quantiles."""

    def __init__(self, db_path: Optional[str], *, refresh_seconds: int = 600) -> None:
        self._db_path = db_path
        self._refresh = refresh_seconds
        self._cache: Dict[str, _CacheEntry] = {}

    def normalize(self, *, domain: str, raw: float) -> float:
        if not np.isfinite(raw):
            return 1.0
        quantiles = self._quantiles_for(domain)
        if not quantiles:
            return logistic_normalize(raw)
        # Ensure monotonic ordering by raw value
        ordered = sorted(quantiles, key=lambda item: item[0])
        values = np.array([val for val, _ in ordered], dtype=float)
        probs = np.array([prob for _, prob in ordered], dtype=float)
        if values.size == 0:
            return logistic_normalize(raw)
        # Handle degenerate cases where all quantile values equal
        if np.allclose(values, values[0]):
            return float(np.clip(np.mean(probs), 0.0, 1.0))
        mapped = np.interp(raw, values, probs, left=0.0, right=1.0)
        return float(np.clip(mapped, 0.0, 1.0))

    # Internal helpers -------------------------------------------------

    def _quantiles_for(self, domain: str) -> QuantileSeries:
        domain_key = (domain or "default").lower()
        cached = self._cache.get(domain_key)
        now = time.time()
        if cached and (now - cached.ts) < self._refresh:
            return cached.quantiles
        quantiles = self._load_quantiles(domain_key)
        self._cache[domain_key] = _CacheEntry(quantiles=quantiles, ts=now)
        return quantiles

    def _load_quantiles(self, domain: str) -> QuantileSeries:
        if not self._db_path:
            return []
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
        except Exception:
            return []
        try:
            row = conn.execute(
                "SELECT snne_quantiles FROM cp_reference WHERE domain=?",
                (domain,),
            ).fetchone()
        except Exception:
            conn.close()
            return []
        finally:
            conn.close()
        if not row:
            return []
        raw_json = row["snne_quantiles"]
        if not raw_json:
            return []
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        return _parse_quantiles(payload)


def _parse_quantiles(payload: Dict[str, float]) -> QuantileSeries:
    items: List[Tuple[float, float]] = []
    for key, value in payload.items():
        try:
            prob = float(key)
            val = float(value)
        except (TypeError, ValueError):
            continue
        if np.isnan(val) or np.isnan(prob):
            continue
        items.append((val, np.clip(prob, 0.0, 1.0)))
    return items

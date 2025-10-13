from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, Optional


Quantiles = Dict[str, float]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_reference(
    db_path: str,
    *,
    domain: str,
    run_id: str,
    target_mis: float,
    tau: Optional[float],
    stats: Dict[str, Any],
    snne_quantiles: Quantiles,
) -> None:
    """Persist CP reference stats for a domain."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO cp_reference (domain, run_id, target_mis, tau, stats_json, snne_quantiles, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
              run_id=excluded.run_id,
              target_mis=excluded.target_mis,
              tau=excluded.tau,
              stats_json=excluded.stats_json,
              snne_quantiles=excluded.snne_quantiles,
              updated=excluded.updated
            """,
            (
                domain,
                run_id,
                float(target_mis),
                float(tau) if tau is not None else None,
                json.dumps(stats, separators=(",", ":")),
                json.dumps(snne_quantiles, separators=(",", ":")),
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_reference(db_path: str, domain: str) -> Optional[Dict[str, Any]]:
    """Return the stored CP reference for a domain, if any."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT domain, run_id, target_mis, tau, stats_json, snne_quantiles, updated FROM cp_reference WHERE domain=?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        stats = json.loads(row["stats_json"]) if row["stats_json"] else {}
        quantiles = json.loads(row["snne_quantiles"]) if row["snne_quantiles"] else {}
        return {
            "domain": row["domain"],
            "run_id": row["run_id"],
            "target_mis": row["target_mis"],
            "tau": row["tau"],
            "stats": stats,
            "snne_quantiles": quantiles,
            "updated": row["updated"],
        }
    finally:
        conn.close()


def quantiles_from_scores(
    scores: Iterable[float], buckets: Iterable[float]
) -> Quantiles:
    """Compute quantiles for SNNE-derived scores."""
    values = [float(s) for s in scores]
    if not values:
        return {}
    import numpy as np

    qs = list(buckets)
    arr = np.array(values, dtype=float)
    result: Quantiles = {}
    for q in qs:
        try:
            val = float(np.quantile(arr, q))
        except Exception:
            continue
        result[f"{q:.2f}"] = val
    return result

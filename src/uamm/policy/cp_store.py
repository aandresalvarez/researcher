import sqlite3
import time
import uuid
from typing import Iterable, Optional, Tuple
from typing import Dict


def add_artifacts(
    db_path: str,
    *,
    run_id: str,
    domain: str,
    items: Iterable[Tuple[float, bool, bool]],
) -> int:
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        ts = time.time()
        rows = [
            (
                str(uuid.uuid4()),
                ts,
                run_id,
                domain,
                float(S),
                int(accepted),
                int(correct),
            )
            for (S, accepted, correct) in items
        ]
        con.executemany(
            "INSERT INTO cp_artifacts (id, ts, run_id, domain, S, accepted, correct) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()
        return len(rows)
    finally:
        con.close()


def compute_threshold(
    db_path: str,
    *,
    domain: str,
    target_mis: float,
    min_accepts: int = 10,
) -> Optional[float]:
    """Compute bootstrap threshold τ so false-accept among accepted ≤ target.

    Returns None if insufficient data.
    """
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT S, accepted, correct FROM cp_artifacts WHERE domain=?",
            (domain,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    data = [(float(r["S"]), int(r["accepted"]), int(r["correct"])) for r in rows]
    # unique candidate thresholds from observed S values
    Ss = sorted({S for (S, _, _) in data})
    best_tau = None
    for tau in Ss:
        acc = [(S, a, c) for (S, a, c) in data if S >= tau]
        if not acc or len(acc) < min_accepts:
            continue
        accepted = sum(1 for (_, a, _) in acc if a)
        if accepted == 0:
            continue
        false_accept = sum(1 for (_, a, c) in acc if a and not c)
        rate = false_accept / accepted
        if rate <= target_mis:
            if best_tau is None or tau < best_tau:
                best_tau = tau
    return best_tau


def domain_stats(
    db_path: str, domain: Optional[str] = None
) -> Dict[str, Dict[str, float | int]]:
    """Compute per-domain stats: counts, accepted, false-accept, rates.

    If domain is None, returns stats for all domains.
    """
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        if domain is None:
            rows = con.execute(
                "SELECT domain, S, accepted, correct FROM cp_artifacts"
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT domain, S, accepted, correct FROM cp_artifacts WHERE domain=?",
                (domain,),
            ).fetchall()
    finally:
        con.close()
    stats: Dict[str, Dict[str, float | int]] = {}
    for r in rows:
        d = str(r["domain"]) if domain is None else domain
        s = stats.setdefault(
            d,
            {
                "n": 0,
                "accepted": 0,
                "false_accept": 0,
                "rate_accept": 0.0,
                "rate_false_accept": 0.0,
            },
        )
        s["n"] += 1
        if int(r["accepted"]):
            s["accepted"] += 1
            if not int(r["correct"]):
                s["false_accept"] += 1
    for d, s in stats.items():
        n = int(s["n"]) or 1
        acc = int(s["accepted"]) or 0
        s["rate_accept"] = acc / n
        s["rate_false_accept"] = (int(s["false_accept"]) / acc) if acc > 0 else 0.0
    return stats

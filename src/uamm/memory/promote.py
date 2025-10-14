from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict

from uamm.storage.memory import add_memory


def _norm(text: str) -> str:
    s = (text or "").strip().lower()
    s = " ".join(s.split())
    return s


@dataclass
class PromotionStats:
    candidates: int
    promoted: int


def promote_episodic_to_semantic(
    db_path: str,
    *,
    min_support: int = 3,
    limit: int = 10,
    workspace: str | None = None,
) -> PromotionStats:
    """Promote frequently repeated facts into semantic memory.

    Heuristic: scan recent memory rows and group by normalized text; when the
    same text appears >= min_support times under keys that look episodic/fact,
    insert a single `semantic:` memory row (if none exists yet).
    """
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        # Pull recent memory; prefer specific workspace when provided.
        if workspace:
            rows = con.execute(
                "SELECT id, key, text, ts FROM memory WHERE workspace = ? ORDER BY ts DESC LIMIT 500",
                (workspace,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, key, text, ts FROM memory ORDER BY ts DESC LIMIT 500"
            ).fetchall()
        counts: Dict[str, int] = {}
        samples: Dict[str, str] = {}
        for r in rows:
            key = str(r["key"] or "")
            if not (key.startswith("episodic:") or key.startswith("fact:")):
                continue
            norm = _norm(str(r["text"] or ""))
            if not norm:
                continue
            counts[norm] = counts.get(norm, 0) + 1
            if norm not in samples:
                samples[norm] = str(r["text"])

        # Identify candidates
        cand = [(c, t) for t, c in counts.items() if c >= int(max(1, min_support))]
        cand.sort(key=lambda x: x[0], reverse=True)
        promoted = 0
        for _, norm in cand[: int(max(1, limit))]:
            text = samples.get(norm)
            if not text:
                continue
            # Avoid duplicates: check if a semantic row already exists
            existing = con.execute(
                "SELECT id FROM memory WHERE key = ? AND text = ? LIMIT 1",
                ("semantic:", text),
            ).fetchone()
            if existing:
                continue
            add_memory(
                db_path,
                key="semantic:",
                text=text,
                domain="summary",
                workspace=workspace,
                created_by="system:promotion",
            )
            promoted += 1
        return PromotionStats(candidates=len(cand), promoted=promoted)
    finally:
        con.close()


__all__ = ["promote_episodic_to_semantic", "PromotionStats"]

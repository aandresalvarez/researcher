import sqlite3
import time
import uuid
from typing import Any, Dict, List, Tuple


def add_memory(
    db_path: str,
    *,
    key: str,
    text: str,
    domain: str = "fact",
    embedding: bytes | None = None,
    recency: float | None = None,
    tokens: int | None = None,
    embedding_model: str | None = None,
) -> str:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        mid = str(uuid.uuid4())
        ts = time.time()
        if recency is None:
            recency = ts
        if tokens is None:
            tokens = len(text.split())
        conn.execute(
            """
            INSERT INTO memory (id, ts, key, text, embedding, domain, recency, tokens, embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, ts, key, text, embedding, domain, recency, tokens, embedding_model),
        )
        # Best-effort FTS sync if virtual table exists (triggers also handle it)
        try:
            conn.execute("INSERT INTO memory_fts (id, text) VALUES (?, ?)", (mid, text))
        except Exception:
            pass
        conn.commit()
        return mid
    finally:
        conn.close()


def _score_text(query: str, text: str) -> float:
    q_terms = [t for t in query.lower().split() if t]
    if not q_terms:
        return 0.0
    t_lower = text.lower()
    hits = sum(1 for t in q_terms if t in t_lower)
    return hits / max(len(q_terms), 1)


def search_memory(db_path: str, q: str, k: int = 5) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        # Prefer FTS5 if available
        try:
            fts_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'"
            ).fetchone()
            if fts_exists:
                q_str = q.strip()
                rows = conn.execute(
                    "SELECT m.id, m.text FROM memory_fts f JOIN memory m ON m.id=f.id WHERE f MATCH ? LIMIT ?",
                    (q_str, k),
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for r in rows:
                    out.append(
                        {
                            "id": r["id"],
                            "snippet": r["text"][:240],
                            "why": "fts5 match",
                            "score": 1.0,
                        }
                    )
                return out
        except Exception:
            # fallback below
            pass

        # naive scan fallback
        rows = conn.execute(
            "SELECT id, text, domain, ts FROM memory ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        scored: List[Tuple[float, sqlite3.Row]] = []
        for r in rows:
            s = _score_text(q, r["text"])
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: List[Dict[str, Any]] = []
        for s, r in scored[:k]:
            snippet = r["text"][:240]
            out.append(
                {
                    "id": r["id"],
                    "snippet": snippet,
                    "why": "term overlap",
                    "score": float(s),
                }
            )
        return out
    finally:
        conn.close()

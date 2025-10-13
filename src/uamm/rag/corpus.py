import json
import sqlite3
import time
import uuid
from ast import literal_eval
from typing import Any, Dict, List, Tuple


def add_doc(
    db_path: str,
    *,
    title: str,
    url: str | None,
    text: str,
    meta: Dict[str, Any] | None = None,
) -> str:
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        did = str(uuid.uuid4())
        ts = time.time()
        meta_blob = json.dumps(meta or {}, separators=(",", ":"))
        con.execute(
            "INSERT INTO corpus (id, ts, title, url, text, meta) VALUES (?, ?, ?, ?, ?, ?)",
            (did, ts, title, url, text, meta_blob),
        )
        try:
            con.execute(
                "INSERT INTO corpus_fts (id, title, text) VALUES (?, ?, ?)",
                (did, title, text),
            )
        except Exception:
            pass
        con.commit()
        return did
    finally:
        con.close()


def _parse_meta(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return literal_eval(raw)
            except Exception:
                return {}
    return {}


def _score_text(query: str, text: str) -> float:
    q_terms = [t for t in query.lower().split() if t]
    if not q_terms:
        return 0.0
    t_lower = text.lower()
    hits = sum(1 for t in q_terms if t in t_lower)
    return hits / max(len(q_terms), 1)


def search_docs(db_path: str, q: str, k: int = 5) -> List[Dict[str, Any]]:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        # prefer FTS5
        try:
            fts_exists = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='corpus_fts'"
            ).fetchone()
            if fts_exists:
                rows = con.execute(
                    "SELECT c.id, c.title, c.url, c.text, c.meta FROM corpus_fts f JOIN corpus c ON c.id=f.id WHERE f MATCH ? LIMIT ?",
                    (q.strip(), k),
                ).fetchall()
                return [
                    {
                        "id": r["id"],
                        "snippet": (r["title"] + ": " + r["text"])[:240],
                        "why": "fts5 match",
                        "score": 1.0,
                        "url": r["url"],
                        "title": r["title"],
                        "meta": _parse_meta(r["meta"]),
                    }
                    for r in rows
                ]
        except Exception:
            pass
        # fallback: naive scan
        rows = con.execute(
            "SELECT id, title, url, text, meta FROM corpus ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        scored: List[Tuple[float, sqlite3.Row]] = []
        for r in rows:
            s = _score_text(q, (r["title"] or "") + " " + (r["text"] or ""))
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: List[Dict[str, Any]] = []
        for s, r in scored[:k]:
            out.append(
                {
                    "id": r["id"],
                    "snippet": ((r["title"] or "") + ": " + (r["text"] or ""))[:240],
                    "why": "term overlap",
                    "score": float(s),
                    "url": r["url"],
                    "title": r["title"],
                    "meta": _parse_meta(r["meta"]),
                }
            )
        return out
    finally:
        con.close()


def fetch_docs_by_ids(db_path: str, doc_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not doc_ids:
        return {}
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in doc_ids)
        rows = con.execute(
            f"SELECT id, title, url, text, meta FROM corpus WHERE id IN ({placeholders})",
            doc_ids,
        ).fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            text = row["text"] or ""
            title = row["title"] or ""
            snippet = (title + ": " + text) if title else text
            snippet = snippet[:240]
            result[row["id"]] = {
                "id": row["id"],
                "title": title,
                "url": row["url"],
                "text": text,
                "snippet": snippet,
                "meta": _parse_meta(row["meta"]),
            }
        return result
    finally:
        con.close()

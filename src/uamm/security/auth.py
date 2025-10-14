from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass
class APIKeyRecord:
    id: str
    workspace: str
    key_hash: str
    role: str
    label: str
    active: bool
    created: float


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_key(prefix: str = "wk_", length: int = 24) -> str:
    raw = uuid.uuid4().hex + os.urandom(8).hex()
    token = prefix + hashlib.sha256(raw.encode()).hexdigest()[:length]
    return token


def create_workspace(
    conn: sqlite3.Connection, slug: str, name: Optional[str] = None, root: Optional[str] = None
) -> str:
    ws_id = str(uuid.uuid4())
    ts = time.time()
    # Try to insert with root column; fall back if column is absent
    try:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, slug, name, created, root) VALUES (?, ?, ?, ?, ?)",
            (ws_id, slug, name or slug, ts, root),
        )
    except Exception:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, slug, name, created) VALUES (?, ?, ?, ?)",
            (ws_id, slug, name or slug, ts),
        )
    conn.commit()
    return ws_id


def issue_api_key(db_path: str, *, workspace: str, role: str, label: str, prefix: str = "wk_") -> str:
    conn = _connect(db_path)
    try:
        # ensure workspace exists
        create_workspace(conn, workspace, name=workspace)
        token = new_key(prefix=prefix)
        kh = hash_key(token)
        ts = time.time()
        kid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO workspace_keys (id, workspace, key_hash, role, label, active, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kid, workspace, kh, role, label, 1, ts),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def lookup_key(db_path: str, token: str) -> Optional[APIKeyRecord]:
    conn = _connect(db_path)
    try:
        kh = hash_key(token)
        row = conn.execute(
            "SELECT id, workspace, key_hash, role, label, active, created FROM workspace_keys WHERE key_hash = ?",
            (kh,),
        ).fetchone()
        if not row:
            return None
        return APIKeyRecord(
            id=row["id"],
            workspace=row["workspace"],
            key_hash=row["key_hash"],
            role=row["role"],
            label=row["label"],
            active=bool(row["active"]),
            created=float(row["created"]),
        )
    finally:
        conn.close()


def list_keys(db_path: str, *, workspace: str) -> list[APIKeyRecord]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, workspace, key_hash, role, label, active, created FROM workspace_keys WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        out: list[APIKeyRecord] = []
        for r in rows:
            out.append(
                APIKeyRecord(
                    id=r["id"],
                    workspace=r["workspace"],
                    key_hash=r["key_hash"],
                    role=r["role"],
                    label=r["label"],
                    active=bool(r["active"]),
                    created=float(r["created"]),
                )
            )
        return out
    finally:
        conn.close()


def deactivate_key(db_path: str, *, key_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE workspace_keys SET active = 0 WHERE id = ?", (key_id,))
        conn.commit()
    finally:
        conn.close()


def list_workspaces(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        try:
            rows = conn.execute("SELECT id, slug, name, created, root FROM workspaces").fetchall()
        except Exception:
            rows = conn.execute("SELECT id, slug, name, created FROM workspaces").fetchall()
        out: list[dict] = []
        for r in rows:
            item = dict(
                id=r["id"],
                slug=r["slug"],
                name=r["name"],
                created=float(r["created"]) if r["created"] is not None else None,
            )
            if "root" in r.keys():  # type: ignore[attr-defined]
                item["root"] = r["root"]
            out.append(item)
        return out
    finally:
        conn.close()


def get_workspace(db_path: str, slug: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        try:
            r = conn.execute(
                "SELECT id, slug, name, created, root FROM workspaces WHERE slug=?",
                (slug,),
            ).fetchone()
        except Exception:
            r = conn.execute(
                "SELECT id, slug, name, created FROM workspaces WHERE slug=?",
                (slug,),
            ).fetchone()
        if not r:
            return None
        out = dict(
            id=r["id"],
            slug=r["slug"],
            name=r["name"],
            created=float(r["created"]) if r["created"] is not None else None,
        )
        if "root" in r.keys():  # type: ignore[attr-defined]
            out["root"] = r["root"]
        return out
    finally:
        conn.close()


def parse_bearer(auth_header: str | None) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def count_keys(db_path: str, *, workspace: str | None = None) -> int:
    conn = _connect(db_path)
    try:
        if workspace:
            row = conn.execute("SELECT COUNT(*) FROM workspace_keys WHERE workspace=? AND active=1", (workspace,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM workspace_keys WHERE active=1").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def insert_api_key(db_path: str, *, workspace: str, role: str, label: str, token: str) -> None:
    conn = _connect(db_path)
    try:
        create_workspace(conn, workspace, name=workspace, root=None)
        kh = hash_key(token)
        ts = time.time()
        kid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO workspace_keys (id, workspace, key_hash, role, label, active, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kid, workspace, kh, role, label, 1, ts),
        )
        conn.commit()
    finally:
        conn.close()

from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional
import sqlite3

from uamm.config.settings import Settings
from uamm.storage.db import ensure_schema


def normalize_root(path: str) -> str:
    p = Path(path).expanduser().resolve()
    return str(p)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child = child.resolve()
        parent = parent.resolve()
    except Exception:
        return False
    return parent == child or parent in child.parents


def ensure_allowed_root(path: str, base_dirs: tuple[str, ...], restrict: bool) -> None:
    """Raise ValueError if `path` is outside allowed bases when `restrict` is True.

    If `restrict` is False or `base_dirs` is empty, no restriction is enforced.
    """
    if not restrict or not base_dirs:
        return
    target = Path(path).resolve()
    bases = [Path(b).expanduser().resolve() for b in base_dirs if b]
    if not any(_is_within(target, base) for base in bases):
        raise ValueError("workspace_root_outside_allowed_bases")


def ensure_workspace_fs(root: str, schema_path: str) -> str:
    """Create per-workspace folders and initialize the SQLite DB.

    Returns the path to `<root>/uamm.sqlite`.
    """
    root_path = Path(root).resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    # Subfolders
    (root_path / "docs").mkdir(parents=True, exist_ok=True)
    (root_path / "vectors").mkdir(parents=True, exist_ok=True)
    # DB
    db_path = root_path / "uamm.sqlite"
    ensure_schema(str(db_path), schema_path)
    return str(db_path)


def get_workspace_record(index_db: str, slug: str) -> Optional[dict]:
    con = sqlite3.connect(index_db)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT id, slug, name, created, root FROM workspaces WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "created": float(row["created"]) if row["created"] is not None else None,
            "root": row["root"],
        }
    finally:
        con.close()


def resolve_paths(index_db: str, slug: str, settings: Settings) -> Dict[str, str]:
    """Resolve effective paths for a workspace.

    If `workspaces.root` is set, derive per-workspace paths. Otherwise, fall back to settings.
    """
    rec = get_workspace_record(index_db, slug)
    if not rec or not rec.get("root"):
        # Fallback: single DB/docs
        return {
            "db_path": settings.db_path,
            "docs_dir": settings.docs_dir,
            "lancedb_uri": settings.lancedb_uri,
        }
    root = Path(str(rec["root"]).strip()).expanduser().resolve()
    db_path = root / "uamm.sqlite"
    docs_dir = root / "docs"
    lancedb_uri = root / "vectors"
    return {
        "db_path": str(db_path),
        "docs_dir": str(docs_dir),
        "lancedb_uri": str(lancedb_uri),
    }

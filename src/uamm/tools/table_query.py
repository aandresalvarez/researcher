from typing import Any, List, Sequence, Tuple
import sqlite3
import time
from uamm.security.sql_guard import is_read_only_select


def table_query(
    db_path: str,
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    max_rows: int | None = None,
    time_limit_ms: int | None = None,
) -> List[Tuple]:
    """Run a guarded, read-only SELECT on SQLite (PRD §11.3).

    This function validates the SQL is a simple SELECT and executes with row/byte limits enforced by the caller.
    """
    if not is_read_only_select(sql):
        raise ValueError("disallowed SQL")
    con = sqlite3.connect(db_path, check_same_thread=False)
    try:
        start = time.time()
        if time_limit_ms is not None:
            limit_s = time_limit_ms / 1000.0

            def _progress() -> int:
                if (time.time() - start) > limit_s:
                    return 1  # abort
                return 0

            con.set_progress_handler(_progress, 1000)
        try:
            cur = con.execute(sql, params or [])
            rows = cur.fetchmany(max_rows if max_rows is not None else -1)
        except sqlite3.Error as exc:  # pragma: no cover
            raise ValueError("query failed") from exc
        return rows
    finally:
        con.close()

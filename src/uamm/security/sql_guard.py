import re
from typing import Iterable


_SELECT_ONLY = re.compile(r"^\s*select\s", re.IGNORECASE | re.DOTALL)
_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|with|union)\b",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_\.]*)(?:\s|$)", re.IGNORECASE)


def is_read_only_select(sql: str) -> bool:
    """Conservative guard: only allow SELECT and disallow obvious DDL/DML/PRAGMAs."""
    s = sql.strip()
    if not _SELECT_ONLY.search(s):
        return False
    # disallow statement stacking and comments to reduce SQLi surface
    if ";" in s:
        return False
    if "--" in s or "/*" in s or "*/" in s:
        return False
    if _BLOCKED.search(s):
        return False
    return True


def referenced_tables(sql: str) -> list[str]:
    """Very naive table extractor from FROM clause (first occurrence)."""
    return [m.group(1) for m in _TABLE_RE.finditer(sql)]


def tables_allowed(sql: str, allowed: Iterable[str]) -> bool:
    tabs = set(referenced_tables(sql))
    if not tabs:
        return False
    if not allowed:
        return False
    return tabs.issubset(set(allowed))

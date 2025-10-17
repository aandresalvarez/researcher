from __future__ import annotations

from typing import Any, Dict, Optional, Callable, List

_GLOBAL: "ToolRegistry | None" = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Any] = {}

    def register(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def get(self, name: str) -> Optional[Any]:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        if name in self._tools:
            del self._tools[name]

    def list(self) -> List[str]:
        return sorted(self._tools.keys())


def get_registry() -> ToolRegistry:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = ToolRegistry()
        ensure_builtins(_GLOBAL)
    return _GLOBAL


def ensure_builtins(reg: ToolRegistry | None = None) -> ToolRegistry:
    """Register built-in tools if not already present.

    Names and expected call signatures:
      - WEB_SEARCH(q: str, k: int = 3) -> list
      - WEB_FETCH(url: str, policy) -> dict
      - MATH_EVAL(expr: str) -> float
      - TABLE_QUERY(db_path: str, sql: str, params: list, max_rows: int | None, time_limit_ms: int | None) -> list
    """
    reg = reg or get_registry()
    # Avoid re-registering
    missing = set(["WEB_SEARCH", "WEB_FETCH", "MATH_EVAL", "TABLE_QUERY"]) - set(
        reg.list()
    )
    if not missing:
        return reg
    if "WEB_SEARCH" in missing:
        from uamm.tools.web_search import web_search

        reg.register("WEB_SEARCH", web_search)
    if "WEB_FETCH" in missing:
        from uamm.tools.web_fetch import web_fetch

        reg.register("WEB_FETCH", web_fetch)
    if "MATH_EVAL" in missing:
        from uamm.tools.math_eval import math_eval

        reg.register("MATH_EVAL", math_eval)
    if "TABLE_QUERY" in missing:
        from uamm.tools.table_query import table_query

        reg.register("TABLE_QUERY", table_query)
    return reg


def import_callable(path: str) -> Callable[..., Any]:
    """Import a callable from a 'module:attr' or 'module.attr' path."""
    mod_path: str
    attr: str
    if ":" in path:
        mod_path, attr = path.split(":", 1)
    else:
        parts = path.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError("invalid path; use module:attr or module.attr")
        mod_path, attr = parts
    mod = __import__(mod_path, fromlist=[attr])
    fn = getattr(mod, attr)
    if not callable(fn):
        raise TypeError("target is not callable")
    return fn


__all__ = ["ToolRegistry", "get_registry", "ensure_builtins", "import_callable"]

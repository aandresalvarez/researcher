from __future__ import annotations

from pathlib import Path
from typing import List


def extract_pdf_tables(path: Path) -> List[str]:
    """Best-effort PDF table extraction using pdfplumber when available.

    Returns a list of simple text tables (rows joined by ' | '). On failure,
    returns an empty list.
    """
    tables: List[str] = []
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return tables
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_table()
                except Exception:
                    t = None
                if not t:
                    continue
                lines: List[str] = []
                for row in t:
                    try:
                        cells = [
                            c.strip() if isinstance(c, str) else "" for c in (row or [])
                        ]
                        lines.append(" | ".join(cells))
                    except Exception:
                        continue
                if lines:
                    tables.append("\n".join(lines))
    except Exception:
        return []
    return tables


__all__ = ["extract_pdf_tables"]

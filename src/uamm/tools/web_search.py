from __future__ import annotations

from typing import List, TypedDict
import json
import os
from pathlib import Path
import re


class WebResult(TypedDict):
    title: str
    url: str
    snippet: str


def web_search(q: str, k: int = 3) -> List[WebResult]:
    """Deterministic search stub backed by optional fixture data."""
    fixture_path = os.getenv("UAMM_WEB_SEARCH_FIXTURE")
    if fixture_path:
        try:
            raw = Path(fixture_path).read_text(encoding="utf-8")
            data = json.loads(raw)
            results = [
                WebResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("snippet", "")),
                )
                for item in data
            ]
            if not q:
                return results[:k]
            q_terms = [term.lower() for term in re.split(r"\s+", q) if term]
            scored: list[tuple[int, WebResult]] = []
            for res in results:
                snippet = res["snippet"].lower()
                title = res["title"].lower()
                score = sum(1 for t in q_terms if t in snippet or t in title)
                scored.append((score, res))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            ordered = [res for _, res in scored]
            return ordered[:k]
        except Exception:
            return []
    return []

from __future__ import annotations

from typing import Iterable, List


def _clean(text: str) -> str:
    return (text or "").strip()


_TEMPLATES = [
    "{base}",
    "{base} (question: {question})",
    "In summary: {base}",
    "{base} — sourced from evidence: {evidence}",
    "Answering '{question}': {base}",
    "{base}. Key evidence: {evidence}",
    "{base} (context: {evidence})",
    "{base}. Confidence rests on: {evidence}",
]


def generate_answer_variants(
    base_answer: str,
    *,
    question: str,
    evidence_snippets: Iterable[str] | None = None,
    count: int = 5,
) -> List[str]:
    """Heuristically generate short paraphrases for SNNE sampling (PRD §7.2)."""
    clean_base = _clean(base_answer) or "No grounded answer yet."
    clean_question = _clean(question) or "Unknown question"
    ev_list = [_clean(e) for e in (evidence_snippets or []) if _clean(e)]
    if not ev_list:
        ev_list = ["no supporting evidence available"]
    variants: List[str] = []
    idx = 0
    template_count = len(_TEMPLATES)
    while len(variants) < max(2, count):
        tmpl = _TEMPLATES[idx % template_count]
        ev = ev_list[idx % len(ev_list)]
        rendered = tmpl.format(base=clean_base, question=clean_question, evidence=ev)
        if idx == 0:
            rendered = clean_base
        if not variants or rendered != variants[-1]:
            variants.append(rendered)
        idx += 1
        if idx > 20 * max(2, count):
            break
    # ensure unique-ish variants by truncating duplicates
    seen = set()
    unique: List[str] = []
    for v in variants:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(v)
    # pad if dedupe shortened list
    while len(unique) < max(2, count):
        unique.append(f"{clean_base} (variant {len(unique) + 1})")
    return unique[: max(2, count)]

from typing import List, Optional


def build_refined_answer(
    *,
    question: str,
    previous_answer: str,
    issues_remaining: List[str],
    context_snippets: Optional[List[str]] = None,
    fetch_url: Optional[str] = None,
    math_value: Optional[float] = None,
    math_text: Optional[str] = None,
    table_text: Optional[str] = None,
) -> str:
    """Compose a concise refined answer paragraph using available signals.

    Prioritizes clarity and provenance: short evidence sentence, computed values, table insights, and source.
    Falls back to previous answer when little context is available.
    """
    parts: List[str] = []
    # Evidence lead
    if context_snippets:
        lead = context_snippets[0].strip()
        parts.append(f"Based on evidence: '{lead}'.")
    # Computation
    if math_text is not None:
        parts.append(f"Computed value: {math_text}.")
    elif math_value is not None:
        parts.append(f"Computed value: {math_value}.")
    if table_text:
        parts.append(f"Table result: {table_text}.")
    # Source
    if fetch_url:
        parts.append(f"Source: {fetch_url}.")
    # Remaining issues
    remaining = [i for i in issues_remaining if i]
    if remaining:
        parts.append(f"Remaining issues: {', '.join(remaining)}.")
    # Fallback if nothing assembled
    if not parts:
        parts.append(previous_answer or "Refined answer pending.")
    return " ".join(parts)

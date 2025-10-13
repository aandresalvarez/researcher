from typing import List, Optional
from uamm.security.prompt_guard import sanitize_fragment


def build_refinement_prompt(
    *,
    question: str,
    previous_answer: str,
    issues: List[str],
    context_snippets: Optional[List[str]] = None,
    fetch_url: Optional[str] = None,
    fetch_snippet: Optional[str] = None,
    math_value: Optional[float] = None,
) -> str:
    """Build a refinement prompt per PRD §7.5 (augmented with context).

    Includes:
    - explicit issues list
    - tool affordances
    - helpful context snippets (pack/fetch)
    - question and previous_answer
    """
    issues_text = (
        "\n".join(f"- {sanitize_fragment(i) or '[filtered]'}" for i in issues)
        if issues
        else "(none)"
    )
    ctx_lines: List[str] = []
    if context_snippets:
        for i, s in enumerate(context_snippets[:3], start=1):
            ctx_lines.append(f"{i}. {sanitize_fragment(s) or '[filtered]'}")
    if fetch_snippet:
        ctx_lines.append(f"Fetch: {sanitize_fragment(fetch_snippet) or '[filtered]'}")
    if math_value is not None:
        ctx_lines.append(f"Math: computed value = {math_value}")
    context_text = "\n".join(ctx_lines) if ctx_lines else "(none)"
    safe_url = sanitize_fragment(fetch_url) if fetch_url else None
    url_hint = f" (consider citing: {safe_url})" if safe_url else ""
    safe_question = sanitize_fragment(question) or question
    safe_previous = sanitize_fragment(previous_answer) or previous_answer

    return (
        "Improve your previous answer using these explicit issues:\n"
        f"{issues_text}\n\n"
        "You MAY use tools:\n"
        "- WEB_SEARCH/WEB_FETCH to find citations/source/date,\n"
        "- MATH_EVAL for calculations,\n"
        "- TABLE_QUERY for DB counts.\n\n"
        f"Helpful context{url_hint}:\n{context_text}\n\n"
        "Question:\n"
        f"{safe_question}\n\n"
        "Previous answer:\n"
        f"{safe_previous}\n\n"
        "Return a corrected, concise answer with citations where relevant."
    )

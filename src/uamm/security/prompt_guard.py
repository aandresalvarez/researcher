from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional
import logging
import re


_LOGGER = logging.getLogger("uamm.security")


@dataclass(slots=True)
class PromptInjectionFinding:
    """Represents a single prompt-injection match within tool output."""

    pattern: str
    span: tuple[int, int]
    excerpt: str


_KEYWORD_SNIPPETS = (
    "ignore previous instruction",
    "ignore previous instructions",
    "ignore previous command",
    "ignore previous commands",
    "ignore all instruction",
    "ignore all instructions",
    "ignore all previous instruction",
    "ignore all previous instructions",
    "forget previous instruction",
    "forget previous instructions",
    "bypass safety",
    "system prompt",
    "override instruction",
    "override instructions",
    "delete all instructions",
    "run shell",
)

_INJECTION_PATTERNS: Iterable[re.Pattern[str]] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"ignore\s+(?:all|any|previous|prior|earlier)\s+(?:instruction|instructions?)",
        r"ignore\s+(?:all|any|previous|prior|earlier)\s+(?:command|commands?)",
        r"forget\s+(?:all|any|previous|prior|earlier)\s+instructions?",
        r"system\s+prompt",
        r"(?:override|bypass).{0,15}instruction",
        r"(?:begin|end)\s+prompt",
        r"run\s+shell",
        r"sudo\s",
        r"rm\s+-rf",
    )
)


class PromptInjectionError(ValueError):
    """Raised when prompt-injection content is detected in a tool payload."""

    def __init__(self, source: str, findings: List[PromptInjectionFinding]) -> None:
        super().__init__(f"prompt injection detected in {source}")
        self.source = source
        self.findings = findings

    def to_meta(self) -> dict:
        primary = self.findings[0] if self.findings else None
        return {
            "source": self.source,
            "patterns": [f.pattern for f in self.findings],
            "excerpt": primary.excerpt if primary else None,
            "span": primary.span if primary else None,
        }


def _build_excerpt(text: str, start: int, end: int, *, radius: int = 40) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].strip()
    return re.sub(r"\s+", " ", snippet)


def detect_prompt_injection(text: str) -> List[PromptInjectionFinding]:
    """Detect suspicious prompt-injection instructions within text."""
    if not text:
        return []
    lowered = text.lower()
    findings: List[PromptInjectionFinding] = []
    for keyword in _KEYWORD_SNIPPETS:
        idx = lowered.find(keyword)
        if idx >= 0:
            span = (idx, idx + len(keyword))
            findings.append(
                PromptInjectionFinding(
                    pattern=keyword,
                    span=span,
                    excerpt=_build_excerpt(text, *span),
                )
            )
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            span = match.span()
            findings.append(
                PromptInjectionFinding(
                    pattern=pattern.pattern,
                    span=span,
                    excerpt=_build_excerpt(text, *span),
                )
            )
    # deduplicate overlapping findings by span
    deduped: List[PromptInjectionFinding] = []
    seen = set()
    for finding in findings:
        if finding.span in seen:
            continue
        seen.add(finding.span)
        deduped.append(finding)
    return deduped


def ensure_safe_tool_text(text: str, *, source: str) -> None:
    """Ensure tool output is free from prompt-injection instructions."""
    findings = detect_prompt_injection(text)
    if findings:
        _LOGGER.warning(
            "prompt_injection_blocked",
            extra={
                "source": source,
                "patterns": [f.pattern for f in findings],
            },
        )
        raise PromptInjectionError(source, findings)


def sanitize_fragment(text: Optional[str]) -> Optional[str]:
    """Sanitize short fragments before placing into prompts."""
    if text is None:
        return None
    fragment = text.strip()
    if not fragment:
        return fragment
    findings = detect_prompt_injection(fragment)
    if findings:
        return "[filtered]"
    # collapse whitespace for cleaner inclusion
    return re.sub(r"\s+", " ", fragment)


__all__ = [
    "PromptInjectionError",
    "PromptInjectionFinding",
    "detect_prompt_injection",
    "ensure_safe_tool_text",
    "sanitize_fragment",
]

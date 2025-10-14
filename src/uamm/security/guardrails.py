from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


_DEFAULT_BLOCK_TERMS = [
    "ignore previous",
    "disregard all instructions",
    "rm -rf",
    "drop database",
    "bypass safety",
    "prompt injection",
]

_DEFAULT_DENY_REGEX = [
    r"(?i)passwd",
    r"(?i)shadow",
    r"(?i)aws_access_key_id",
]


@dataclass
class GuardrailsConfig:
    block_terms: List[str]
    deny_regex: List[str]

    @staticmethod
    def load(path: Optional[str]) -> "GuardrailsConfig":
        if not path or not yaml:
            return GuardrailsConfig(block_terms=list(_DEFAULT_BLOCK_TERMS), deny_regex=list(_DEFAULT_DENY_REGEX))
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            block_terms = list(data.get("block_terms", _DEFAULT_BLOCK_TERMS) or [])
            deny_regex = list(data.get("deny_regex", _DEFAULT_DENY_REGEX) or [])
            return GuardrailsConfig(block_terms=block_terms, deny_regex=deny_regex)
        except Exception:
            return GuardrailsConfig(block_terms=list(_DEFAULT_BLOCK_TERMS), deny_regex=list(_DEFAULT_DENY_REGEX))


def _check_terms(text: str, terms: List[str]) -> List[str]:
    low = (text or "").lower()
    hits: List[str] = []
    for t in terms:
        try:
            if t and t.lower() in low:
                hits.append(t)
        except Exception:
            continue
    return hits


def _check_regex(text: str, patterns: List[str]) -> List[str]:
    out: List[str] = []
    try:
        import re
    except Exception:
        return out
    for pat in patterns:
        try:
            if re.search(pat, text or ""):
                out.append(pat)
        except re.error:
            continue
    return out


def pre_guard(text: str, *, config: GuardrailsConfig) -> Tuple[bool, List[str]]:
    """Pre-generation guard. Returns (ok, violations)."""
    violations: List[str] = []
    violations += [f"term:{t}" for t in _check_terms(text, config.block_terms)]
    violations += [f"re:{r}" for r in _check_regex(text, config.deny_regex)]
    return (len(violations) == 0), violations


def post_guard(answer: str, *, config: GuardrailsConfig) -> Tuple[bool, List[str]]:
    """Post-generation guard. Returns (ok, violations)."""
    violations: List[str] = []
    violations += [f"term:{t}" for t in _check_terms(answer, config.block_terms)]
    violations += [f"re:{r}" for r in _check_regex(answer, config.deny_regex)]
    return (len(violations) == 0), violations


__all__ = ["GuardrailsConfig", "pre_guard", "post_guard"]

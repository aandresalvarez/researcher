from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Sequence, Tuple
import re


# Minimal stopword list to stabilize lexical overlap without heavy deps.
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "in",
    "on",
    "for",
    "to",
    "with",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "as",
    "that",
    "this",
    "it",
    "from",
    "at",
    "we",
    "you",
    "they",
    "their",
    "our",
    "your",
}


def _tokens(text: str) -> List[str]:
    clean = (text or "").lower()
    out: List[str] = []
    token = []
    for ch in clean:
        if ch.isalnum():
            token.append(ch)
        else:
            if token:
                out.append("".join(token))
                token = []
    if token:
        out.append("".join(token))
    # filter stopwords and 1-char tokens
    return [t for t in out if len(t) > 1 and t not in _STOPWORDS]


def _dedupe(seq: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _sentences(text: str) -> List[str]:
    # Very light sentence splitter on punctuation and newlines.
    raw = (text or "").replace("\n", ". ")
    parts: List[str] = []
    start = 0
    for i, ch in enumerate(raw):
        if ch in ".!?":
            seg = raw[start : i + 1].strip()
            if seg:
                parts.append(seg)
            start = i + 1
    tail = raw[start:].strip()
    if tail:
        parts.append(tail)
    return _dedupe([p.strip() for p in parts if p.strip()])


def extract_claims(answer: str, *, min_words: int = 4, max_words: int = 50) -> List[str]:
    """Extract simple sentence-level claims from an answer.

    Filters trivial or very long sentences to keep scoring meaningful.
    """
    claims: List[str] = []
    for sent in _sentences(answer or ""):
        # Simple word count without aggressive filtering (keep short tokens)
        raw_words = [w for w in re.split(r"\W+", sent) if w]
        n = len(raw_words)
        if n < min_words or n > max_words:
            continue
        claims.append(sent)
    return claims


def _evidence_texts(pack: Sequence[Any]) -> List[str]:
    texts: List[str] = []
    for item in pack:
        # item may be pydantic model or dict-like
        if isinstance(item, Mapping):
            snippet = str(item.get("snippet", "") or "").strip()
            title = str(item.get("title", "") or "").strip()
        else:
            snippet = str(getattr(item, "snippet", "") or "").strip()
            title = str(getattr(item, "title", "") or "").strip()
        if snippet:
            texts.append(snippet)
        if title and title not in texts:
            texts.append(title)
    return texts


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return float(inter) / float(union) if union else 0.0


def align_claims_to_evidence(
    claims: Sequence[str], evidence_pack: Sequence[Any], *, threshold: float = 0.2
) -> Tuple[int, List[str]]:
    """Return number of supported claims (>= threshold) and unsupported claim texts.

    Support is computed as best Jaccard overlap of content tokens with any evidence snippet/title.
    Claims containing bracketed citations like "[1]" are treated as supported.
    """
    evidence_texts = _evidence_texts(evidence_pack)
    ev_tokens = [
        set(_tokens(text)) for text in evidence_texts if isinstance(text, str) and text
    ]
    supported = 0
    unsupported: List[str] = []
    for claim in claims:
        # consider citations hint
        if "[" in claim and "]" in claim:
            supported += 1
            continue
        ctoks = _tokens(claim)
        best = 0.0
        for etoks in ev_tokens:
            score = _jaccard(ctoks, etoks)
            if score > best:
                best = score
                if best >= threshold:
                    break
        if best >= threshold:
            supported += 1
        else:
            unsupported.append(claim)
    return supported, unsupported


def compute_faithfulness(
    answer: str, evidence_pack: Sequence[Any], *, threshold: float = 0.2
) -> dict:
    """Compute faithfulness score and unsupported claims list.

    Returns a dict with keys: `score` (0..1 or None if no claims),
    `claim_count`, `supported_count`, and `unsupported_claims` (list).
    """
    claims = extract_claims(answer)
    if not claims:
        return {
            "score": None,
            "claim_count": 0,
            "supported_count": 0,
            "unsupported_claims": [],
        }
    supported, unsupported = align_claims_to_evidence(claims, evidence_pack, threshold=threshold)
    score = float(supported) / float(len(claims)) if claims else None
    return {
        "score": score,
        "claim_count": len(claims),
        "supported_count": supported,
        "unsupported_claims": unsupported,
    }


__all__ = [
    "extract_claims",
    "align_claims_to_evidence",
    "compute_faithfulness",
]

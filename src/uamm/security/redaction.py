from typing import Tuple
import re


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+?\d[\d\s\-()]{7,}\d)")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def redact(text: str) -> Tuple[str, bool]:
    """Redact common PII from text. Returns (redacted_text, was_redacted).

    Order matters: apply SSN before phone to avoid phone pattern capturing SSN.
    """
    if not text:
        return text, False
    out0 = SSN_RE.sub("[REDACTED_SSN]", text)
    out1 = EMAIL_RE.sub("[REDACTED_EMAIL]", out0)
    out2 = PHONE_RE.sub("[REDACTED_PHONE]", out1)
    return out2, out2 != text

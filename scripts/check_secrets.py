#!/usr/bin/env python3
"""Pre-commit secret scanner to block committing API keys.

Scans staged text files for common API key patterns (OpenAI, GitHub, Google,
AWS, Slack, Stripe, etc.) and PEM private keys. Prints findings and exits 1
on match. Add `# allow-secret` on the same line to silence a false positive.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Tuple


PATTERNS: tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("GitHub PAT", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("GitHub FG", re.compile(r"github_pat_[0-9A-Za-z_]{80,}")),
    ("Google API", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "AWS Secret Key",
        re.compile(r"(?i)aws(.{0,20})?['\"][0-9a-zA-Z/+]{40}['\"]"),
    ),
    ("Slack Token", re.compile(r"xox[abprs]-[0-9A-Za-z-]{10,48}")),
    ("Stripe Secret", re.compile(r"sk_(live|test)_[0-9A-Za-z]{16,}")),
    ("Twilio Secret", re.compile(r"SK[0-9a-fA-F]{32}")),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA|EC|DSA|OPENSSH) PRIVATE KEY-----")),
)


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "backup",
    "__pycache__",
    ".pytest_cache",
}


def iter_lines(path: Path) -> Iterable[Tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    for i, line in enumerate(text.splitlines(), 1):
        yield i, line


def should_skip(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return True
    try:
        if path.stat().st_size > 1024 * 1024:
            return True
    except Exception:
        return False
    # Skip common binary extensions
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip"}:
        return True
    return False


def main(argv: list[str]) -> int:
    files = [Path(p) for p in argv[1:]] if len(argv) > 1 else []
    if not files:
        return 0
    violations: list[tuple[str, str, int, str]] = []
    for path in files:
        if not path.exists() or should_skip(path):
            continue
        for ln, line in iter_lines(path):
            if "allow-secret" in line:
                continue
            for label, regex in PATTERNS:
                if regex.search(line):
                    snippet = line.strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    violations.append((label, str(path), ln, snippet))
    if not violations:
        return 0
    print("Secret scan failed; potential secrets detected:\n", file=sys.stderr)
    for label, path, ln, snippet in violations:
        print(f" - [{label}] {path}:{ln}: {snippet}", file=sys.stderr)
    print(
        "\nIf this is a false positive, add '# allow-secret' to the line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

from typing import TypedDict
import os
import re
from pathlib import Path
from urllib.parse import urlparse
import httpx
from uamm.security.egress import EgressPolicy, check_url_allowed
from uamm.security.prompt_guard import ensure_safe_tool_text


class FetchResult(TypedDict):
    url: str
    text: str
    meta: dict


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)


def _sanitize_html(html: str) -> str:
    html = _SCRIPT_RE.sub(" ", html)
    html = _STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fixture_lookup(url: str) -> FetchResult | None:
    fixture_dir = os.getenv("UAMM_WEB_FETCH_FIXTURE_DIR")
    if not fixture_dir:
        return None
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    path = parsed.path.strip("/")
    if not path:
        path = "index"
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{host}_{path}")
    candidate = Path(fixture_dir) / f"{sanitized}.html"
    if not candidate.exists():
        fallback = Path(fixture_dir) / f"{host.replace('.', '_')}.html"
        if fallback.exists():
            candidate = fallback
        else:
            return None
    try:
        text = candidate.read_text(encoding="utf-8")
    except Exception:
        return None
    sanitized_text = _sanitize_html(text)
    ensure_safe_tool_text(sanitized_text, source=url)
    return {
        "url": url,
        "text": sanitized_text,
        "meta": {
            "status": 200,
            "content_type": "text/html",
            "bytes": len(text.encode("utf-8")),
            "requested_url": url,
            "policy_result": "allowed",
            "policy_checked": True,
            "injection_blocked": False,
        },
    }


def web_fetch(url: str, policy: EgressPolicy | None = None) -> FetchResult:
    policy = policy or EgressPolicy()
    check_url_allowed(url, policy)
    # host allow/deny enforcement
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allow = (
        [h.lower() for h in policy.allowlist_hosts] if policy.allowlist_hosts else []
    )
    deny = [h.lower() for h in policy.denylist_hosts] if policy.denylist_hosts else []
    if allow and host not in allow:
        raise ValueError("host not allowed")
    if deny and host in deny:
        raise ValueError("host not allowed")
    fixture = _fixture_lookup(url)
    if fixture is not None:
        return fixture
    headers = {"User-Agent": "UAMM-Fetch/0.1"}
    timeout = httpx.Timeout(10.0, read=10.0)
    # httpx does not expose redirect limit in Limits; rely on follow_redirects and content checks
    follow = bool(policy.allow_redirects and policy.allow_redirects > 0)
    max_redirects = int(policy.allow_redirects) if follow else 0
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=follow, max_redirects=max_redirects
        ) as client:
            resp = client.get(url, headers=headers)
            content = resp.text
    except httpx.HTTPError as exc:  # pragma: no cover
        raise ValueError("fetch failed") from exc
    if len(resp.content) > policy.max_payload_bytes:
        raise ValueError("payload too large")
    ctype = resp.headers.get("content-type", "")
    if not ctype.lower().startswith("text/") and "json" not in ctype.lower():
        raise ValueError("unsupported content type")
    text = content if "html" not in ctype.lower() else _sanitize_html(content)
    ensure_safe_tool_text(text, source=str(resp.url))
    return {
        "url": str(resp.url),
        "text": text,
        "meta": {
            "status": resp.status_code,
            "content_type": ctype,
            "bytes": len(resp.content),
            "requested_url": url,
            "policy_result": "allowed",
            "policy_checked": True,
            "injection_blocked": False,
        },
    }

from pathlib import Path

import pytest

from uamm.tools.web_search import web_search
from uamm.tools.web_fetch import web_fetch
from uamm.tools.table_query import table_query
from uamm.security.prompt_guard import PromptInjectionError


FIXTURES = Path("tests/data")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("UAMM_WEB_SEARCH_FIXTURE", raising=False)
    monkeypatch.delenv("UAMM_WEB_FETCH_FIXTURE_DIR", raising=False)


def test_web_search_uses_fixture(monkeypatch):
    monkeypatch.setenv(
        "UAMM_WEB_SEARCH_FIXTURE", str(FIXTURES / "web_search_results.json")
    )
    results = web_search("metrics", k=2)
    assert len(results) == 2
    assert results[0]["title"] == "Example Metrics Report"
    # Ensure ordering favours query term hits
    assert "metrics" in results[0]["snippet"].lower()


def test_web_fetch_returns_sanitized_fixture(monkeypatch):
    monkeypatch.setenv("UAMM_WEB_FETCH_FIXTURE_DIR", str(FIXTURES))
    res = web_fetch("https://example.com")
    assert res["meta"]["status"] == 200
    assert "illustrative examples" in res["text"].lower()
    # ensure html tags removed
    assert "<" not in res["text"]
    # ensure policy metadata propagated
    assert res["meta"]["requested_url"] == "https://example.com"
    assert res["meta"]["policy_result"] == "allowed"
    assert res["meta"]["injection_blocked"] is False


def test_web_fetch_blocks_unlisted_host(monkeypatch):
    monkeypatch.setenv("UAMM_WEB_FETCH_FIXTURE_DIR", str(FIXTURES))
    from uamm.security.egress import EgressPolicy

    policy = EgressPolicy(allowlist_hosts=("allowed.com",))
    with pytest.raises(ValueError):
        web_fetch("https://example.com", policy=policy)


def test_web_fetch_blocks_prompt_injection(monkeypatch):
    monkeypatch.setenv("UAMM_WEB_FETCH_FIXTURE_DIR", str(FIXTURES))
    with pytest.raises(PromptInjectionError):
        web_fetch("https://example.com/injection")


def test_table_query_read_only(tmp_path):
    db_path = FIXTURES / "demo.sqlite"
    rows = table_query(
        str(db_path), "SELECT id, value FROM demo ORDER BY id", max_rows=2
    )
    assert rows == [(1, "foo"), (2, "bar")]
    with pytest.raises(ValueError):
        table_query(str(db_path), "DELETE FROM demo")

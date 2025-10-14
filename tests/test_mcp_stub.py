import os

import pytest

from uamm.mcp.server import get_tool_adapters


def test_mcp_adapters_expose_expected_tools(tmp_path):
    adapters = get_tool_adapters()
    for key in ["WEB_SEARCH", "WEB_FETCH", "MATH_EVAL", "TABLE_QUERY", "UAMM_ANSWER"]:
        assert key in adapters


def test_mcp_math_eval_adapter_executes():
    adapters = get_tool_adapters()
    out = adapters["MATH_EVAL"]("2+2")
    assert out["status"] == "ok"
    assert int(out["value"]) == 4


@pytest.mark.skipif(
    os.getenv("UAMM_SKIP_NETWORK", "1") == "1", reason="Avoid network in CI"
)
def test_mcp_web_search_fixture(monkeypatch):
    # Provide a fixture-backed search
    monkeypatch.setenv("UAMM_WEB_SEARCH_FIXTURE", "tests/data/web_search_results.json")
    adapters = get_tool_adapters()
    out = adapters["WEB_SEARCH"]("report", k=1)
    assert out["status"] == "ok"
    assert len(out["results"]) == 1

from uamm.evals.suites import run_suite
from uamm.config.settings import load_settings


def test_eval_metrics_include_tokens_cost(monkeypatch, tmp_path):
    # Use a temp DB for eval artifacts
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "eval.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    # Set a non-zero token cost for visibility
    monkeypatch.setenv("UAMM_TOKEN_COST_PER_1K", "0.001")
    settings = load_settings()
    res = run_suite("HPQA-S", settings=settings)
    metrics = res.get("metrics", {})
    # We expect keys present; values may be None depending on agent fallback
    assert "avg_tokens" in metrics
    assert "total_cost" in metrics

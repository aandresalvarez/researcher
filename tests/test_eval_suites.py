from uamm.evals.suites import list_suites, run_suite


def test_run_suite_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "suite.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    result = run_suite("UQ-A1", run_id="suite-smoke", update_cp_reference=False)
    assert result["suite_id"] == "UQ-A1"
    assert result["metrics"]["total"] == len(result["records"])


def test_run_suite_cp_updates_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "cp.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    result = run_suite("CP-B1", run_id="cp-suite", update_cp_reference=True)
    reference = result.get("cp_reference")
    assert reference is not None
    assert reference["inserted"] > 0
    assert "domains" in reference


def test_list_suites_non_empty():
    suites = list_suites()
    ids = [suite.id for suite in suites]
    assert "UQ-A1" in ids

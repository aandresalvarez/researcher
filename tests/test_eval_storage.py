from pathlib import Path

from uamm.evals.storage import store_eval_run, fetch_eval_run


def test_store_and_fetch_eval_run(tmp_path: Path):
    db_path = tmp_path / "evals.sqlite"
    from uamm.storage.db import ensure_schema

    ensure_schema(str(db_path), "src/uamm/memory/schema.sql")
    store_eval_run(
        str(db_path),
        run_id="run-1",
        suite_id="UQ-A1",
        metrics={"total": 2},
        by_domain={"default": {"total": 2}},
        records=[{"S": 0.9, "accepted": True, "correct": True}],
    )

    results = fetch_eval_run(str(db_path), "run-1")
    assert len(results) == 1
    assert results[0]["suite_id"] == "UQ-A1"
    assert results[0]["metrics"]["total"] == 2

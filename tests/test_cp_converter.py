from pathlib import Path
import sys

sys.path.insert(0, "src")
from scripts import evals_to_cp
from uamm.storage.db import ensure_schema
from uamm.policy import cp_store


def test_convert_eval_to_cp_rows(tmp_path):
    path = Path("tests/data/demo_eval_output.json")
    rows = evals_to_cp.convert_eval_to_cp(path)
    assert len(rows) == 3
    assert rows[0]["domain"] == "analytics"
    grouped = evals_to_cp._group_rows(rows)
    db_path = tmp_path / "cp.sqlite"
    schema_path = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    total = 0
    for domain, tuples in grouped.items():
        total += cp_store.add_artifacts(
            str(db_path), run_id="test-run", domain=domain, items=tuples
        )
    assert total == 3
    stats = cp_store.domain_stats(str(db_path))
    assert "analytics" in stats and stats["analytics"]["n"] == 2
    assert "biomed" in stats and stats["biomed"]["n"] == 1

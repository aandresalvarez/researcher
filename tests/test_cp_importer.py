import sys
from pathlib import Path

sys.path.insert(0, "src")
from scripts import import_cp_artifacts as importer  # noqa: E402
from uamm.policy import cp_store  # noqa: E402
from uamm.storage.db import ensure_schema  # noqa: E402


def test_importer_load_rows_and_add_artifacts(tmp_path):
    db_path = tmp_path / "cp_seed.sqlite"
    schema_path = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    rows = list(importer._load_rows(Path("tests/data/cp_seed.json")))
    assert len(rows) == 4
    inserted = cp_store.add_artifacts(
        str(db_path), run_id="seed", domain="analytics", items=rows
    )
    assert inserted == 4
    stats = cp_store.domain_stats(str(db_path), domain="analytics")["analytics"]
    assert stats["n"] == 4
    tau = cp_store.compute_threshold(
        str(db_path), domain="analytics", target_mis=0.25, min_accepts=1
    )
    assert tau is not None


def test_compute_threshold_meets_target_miscoverage(tmp_path):
    db_path = tmp_path / "cp_full.sqlite"
    schema_path = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    rows = list(importer._load_rows(Path("tests/data/cp_seed_full.json")))
    cp_store.add_artifacts(
        str(db_path), run_id="seed-full", domain="biomed", items=rows
    )
    target = 0.1
    tau = cp_store.compute_threshold(
        str(db_path), domain="biomed", target_mis=target, min_accepts=5
    )
    assert tau is not None
    accepted = [row for row in rows if row[0] >= tau and row[1]]
    assert accepted, "expected accepted rows after threshold"
    false_accept = sum(1 for row in accepted if not row[2])
    rate = false_accept / len(accepted)
    assert rate <= target + 1e-9

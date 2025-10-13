from pathlib import Path

from uamm.policy.cp_reference import (
    get_reference,
    quantiles_from_scores,
    upsert_reference,
)
from uamm.policy.cp_store import add_artifacts, domain_stats
from uamm.policy.drift import compute_quantile_drift, needs_attention, recent_scores
from uamm.storage.db import ensure_schema


def test_cp_reference_roundtrip(tmp_path):
    db_path = tmp_path / "calib.sqlite"
    schema_path = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    domain = "analytics"
    samples = [
        (0.92, True, True),
        (0.88, True, True),
        (0.83, True, False),
        (0.79, False, False),
    ]
    inserted = add_artifacts(str(db_path), run_id="test", domain=domain, items=samples)
    assert inserted == len(samples)
    baseline_quantiles = quantiles_from_scores(
        [s for (s, _, _) in samples], (0.1, 0.25, 0.5, 0.75, 0.9)
    )
    stats = domain_stats(str(db_path), domain=domain).get(domain, {})
    upsert_reference(
        str(db_path),
        domain=domain,
        run_id="test",
        target_mis=0.05,
        tau=0.84,
        stats=stats,
        snne_quantiles=baseline_quantiles,
    )
    ref = get_reference(str(db_path), domain)
    assert ref is not None
    assert ref["domain"] == domain
    assert ref["tau"] == 0.84
    assert ref["snne_quantiles"] == baseline_quantiles


def test_quantile_drift_detection(tmp_path):
    db_path = tmp_path / "drift.sqlite"
    ensure_schema(str(db_path), "src/uamm/memory/schema.sql")
    domain = "biomed"
    baseline_scores = [0.9, 0.88, 0.87, 0.86, 0.85]
    baseline_quantiles = quantiles_from_scores(baseline_scores, (0.1, 0.5, 0.9))
    stats = {"n": len(baseline_scores), "accepted": len(baseline_scores)}
    upsert_reference(
        str(db_path),
        domain=domain,
        run_id="baseline",
        target_mis=0.05,
        tau=0.85,
        stats=stats,
        snne_quantiles=baseline_quantiles,
    )
    new_samples = [
        (0.72, True, False),
        (0.70, True, False),
        (0.68, True, False),
        (0.65, False, False),
        (0.60, False, False),
    ]
    add_artifacts(str(db_path), run_id="shift", domain=domain, items=new_samples)
    recent = recent_scores(str(db_path), domain, limit=10)
    recent_quantiles = quantiles_from_scores(recent, (0.1, 0.5, 0.9))
    drift = compute_quantile_drift(
        baseline_quantiles, recent_quantiles, sample_size=len(recent)
    )
    assert drift.max_abs_delta > 0.1
    assert needs_attention(drift, tolerance=0.05, min_sample_size=3)

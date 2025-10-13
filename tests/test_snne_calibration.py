from uamm.uq.calibration import SNNECalibrator


def test_calibrator_monotonic(tmp_path):
    db = tmp_path / "calib.sqlite"
    import sqlite3
    from uamm.storage.db import ensure_schema

    ensure_schema(str(db), "src/uamm/memory/schema.sql")
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO cp_reference (domain, run_id, target_mis, tau, stats_json, snne_quantiles, updated) VALUES (?,?,?,?,?,?,?)",
        (
            "analytics",
            "run-test",
            0.05,
            0.82,
            "{}",
            '{"0.10": -1.0, "0.50": 0.0, "0.90": 1.0}',
            0.0,
        ),
    )
    con.commit()
    con.close()
    calibrator = SNNECalibrator(str(db), refresh_seconds=0)
    assert calibrator.normalize(domain="analytics", raw=-2) < calibrator.normalize(
        domain="analytics", raw=0
    )
    # fallback uses logistic for unknown domain
    fallback = calibrator.normalize(domain="unknown", raw=0)
    assert 0.4 < fallback < 0.6

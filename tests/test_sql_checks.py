import sqlite3
from importlib.resources import files

from fastapi.testclient import TestClient

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup_db(tmp_path):
    db_path = tmp_path / "checks.sqlite"
    schema = files("uamm.memory").joinpath("schema.sql")
    ensure_schema(str(db_path), str(schema))
    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, value INTEGER)")
        con.executemany("INSERT INTO demo (value) VALUES (?)", [(1,), (2,), (3,)])
        con.commit()
    finally:
        con.close()
    return str(db_path)


def test_sql_checks_failures_reported(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    # Prepare a policy pack with checks for 'id' column minimum
    pol_dir = tmp_path / "pol"
    pol_dir.mkdir()
    pack = {
        "table_allowed": ["demo"],
        "table_policies": {
            "demo": {
                "checks": {
                    "id": {"min": 2}
                }
            }
        },
    }
    pol_file = pol_dir / "checks.yaml"
    pol_file.write_text(__import__("yaml").safe_dump(pack), encoding="utf-8")

    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_POLICIES_DIR", str(pol_dir))
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()

    admin_key = "wk_admin"
    editor_key = "wk_editor"

    def fake_lookup_key(db_path: str, token: str):
        if token == admin_key:
            return APIKeyRecord(id="1", workspace="ws1", key_hash="h1", role="admin", label="a", active=True, created=0.0)
        if token == editor_key:
            return APIKeyRecord(id="2", workspace="ws1", key_hash="h2", role="editor", label="e", active=True, created=0.0)
        return None

    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    with TestClient(app) as client:
        # Apply policy with checks
        r = client.post(
            "/workspaces/ws1/policies/apply",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"name": "checks"},
        )
        assert r.status_code == 200
        # Execute a query that will include id=1, violating min>=2
        q = client.post(
            "/table/query",
            headers={"Authorization": f"Bearer {editor_key}", "X-Workspace": "ws1"},
            json={"sql": "SELECT id, value FROM demo ORDER BY id", "params": []},
        )
        assert q.status_code == 200
        data = q.json()
        checks = data.get("checks", {})
        assert checks.get("applied")
        assert checks.get("violations")
        assert checks.get("ok") is False

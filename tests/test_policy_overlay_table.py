from fastapi.testclient import TestClient
from pathlib import Path
import yaml

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup(tmp_path):
    db = tmp_path / "pol.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_policy_overlay_applies_to_table_guard(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir()
    # Create a policy that forbids all tables
    with (policies_dir / "denyall.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump({"table_allowed": []}, f)

    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    monkeypatch.setenv("UAMM_POLICIES_DIR", str(policies_dir))
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
        # Apply denyall pack
        r = client.post(
            "/workspaces/ws1/policies/apply",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"name": "denyall"},
        )
        assert r.status_code == 200
        # Now table query should be forbidden regardless of default allow
        q = client.post(
            "/table/query",
            headers={"Authorization": f"Bearer {editor_key}", "X-Workspace": "ws1"},
            json={"sql": "SELECT * FROM demo", "params": []},
        )
        assert q.status_code == 403


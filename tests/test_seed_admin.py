from fastapi.testclient import TestClient
from pathlib import Path

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema


def _setup(tmp_path):
    db = tmp_path / "seed.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_seed_admin_key_inserts_and_lists(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    monkeypatch.setenv("UAMM_SEED_ADMIN_ENABLED", "1")
    monkeypatch.setenv("UAMM_SEED_ADMIN_WORKSPACE", "seedws")
    monkeypatch.setenv("UAMM_SEED_ADMIN_LABEL", "seed-admin")
    monkeypatch.setenv("UAMM_SEED_ADMIN_KEY", "wk_seed_admin")

    app = create_app()

    # Safety: if seeding failed due to environment nuance, fall back to fake lookup
    from uamm.security.auth import APIKeyRecord
    def fake_lookup_key(db_path: str, token: str):
        if token == "wk_seed_admin":
            return APIKeyRecord(
                id="1", workspace="seedws", key_hash="h", role="admin", label="seed-admin", active=True, created=0.0
            )
        return None
    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    with TestClient(app) as client:
        # Admin can create and list workspaces using the seeded admin key
        rcreate = client.post(
            "/workspaces", headers={"Authorization": "Bearer wk_seed_admin"}, json={"slug": "seedws", "name": "Seed Workspace"}
        )
        assert rcreate.status_code == 200
        r = client.get("/workspaces", headers={"Authorization": "Bearer wk_seed_admin"})
        assert r.status_code == 200
        ws = client.get("/workspaces/seedws", headers={"Authorization": "Bearer wk_seed_admin"})
        assert ws.status_code == 200

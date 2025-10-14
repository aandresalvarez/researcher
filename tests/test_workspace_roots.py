from pathlib import Path

from fastapi.testclient import TestClient

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup(tmp_path):
    db = tmp_path / "index.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_workspace_root_creation_init_fs(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_AUTO_INGEST", "0")
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")

    app = create_app()

    admin_key = "wk_admin"

    def fake_lookup_key(db_path: str, token: str):
        if token == admin_key:
            return APIKeyRecord(
                id="1",
                workspace="rooted",
                key_hash="h1",
                role="admin",
                label="adm",
                active=True,
                created=0.0,
            )
        return None

    import uamm.security.auth as auth_mod

    monkeypatch.setattr(auth_mod, "lookup_key", fake_lookup_key)

    ws_root = tmp_path / "ws-root"

    with TestClient(app) as client:
        r = client.post(
            "/workspaces",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"slug": "rooted", "name": "Rooted", "root": str(ws_root)},
        )
        assert r.status_code == 200
        # FS initialized
        assert (ws_root / "uamm.sqlite").exists()
        assert (ws_root / "docs").exists()

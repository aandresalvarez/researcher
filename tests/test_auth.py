from fastapi.testclient import TestClient
from pathlib import Path

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup(tmp_path):
    db = tmp_path / "auth.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_auth_required_blocks_without_key(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/memory", json={"text": "hello", "key": "fact:test"})
        assert r.status_code in (401, 403)


def test_api_key_roles_allow_and_forbid(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()
    viewer_key = "wk_viewer"
    editor_key = "wk_editor"
    other_key = "wk_other"

    def fake_lookup_key(db_path: str, token: str):
        if token == viewer_key:
            return APIKeyRecord(
                id="1",
                workspace="wsA",
                key_hash="h1",
                role="viewer",
                label="viewA",
                active=True,
                created=0.0,
            )
        if token == editor_key:
            return APIKeyRecord(
                id="2",
                workspace="wsA",
                key_hash="h2",
                role="editor",
                label="editA",
                active=True,
                created=0.0,
            )
        if token == other_key:
            return APIKeyRecord(
                id="3",
                workspace="wsB",
                key_hash="h3",
                role="editor",
                label="editB",
                active=True,
                created=0.0,
            )
        return None

    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    with TestClient(app) as client:
        # Viewer cannot write
        r = client.post(
            "/memory",
            headers={"Authorization": f"Bearer {viewer_key}"},
            json={"text": "alpha", "key": "fact:test"},
        )
        assert r.status_code in (401, 403)

        # Editor can write and search within their workspace
        r2 = client.post(
            "/memory",
            headers={"Authorization": f"Bearer {editor_key}"},
            json={"text": "alpha", "key": "fact:test"},
        )
        assert r2.status_code == 200

        s1 = client.get(
            "/memory/search",
            headers={"Authorization": f"Bearer {editor_key}"},
            params={"q": "alpha"},
        )
        assert s1.status_code == 200
        assert s1.json()["hits"], "expected to find alpha in wsA"

        # Other workspace should not see the content
        s2 = client.get(
            "/memory/search",
            headers={"Authorization": f"Bearer {other_key}", "X-Workspace": "wsB"},
            params={"q": "alpha"},
        )
        assert s2.status_code == 200
        assert not s2.json()["hits"], "wsB should not see wsA memory"

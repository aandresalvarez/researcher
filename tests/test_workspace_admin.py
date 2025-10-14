from fastapi.testclient import TestClient
from pathlib import Path

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup(tmp_path):
    db = tmp_path / "ws.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_workspace_member_crud_and_audit(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()

    admin_key = "wk_admin"
    editor_key = "wk_editor"

    def fake_lookup_key(db_path: str, token: str):
        if token == admin_key:
            return APIKeyRecord(
                id="1",
                workspace="team1",
                key_hash="h1",
                role="admin",
                label="admin1",
                active=True,
                created=0.0,
            )
        if token == editor_key:
            return APIKeyRecord(
                id="2",
                workspace="team1",
                key_hash="h2",
                role="editor",
                label="ed1",
                active=True,
                created=0.0,
            )
        return None

    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    with TestClient(app) as client:
        # Create workspace (no-op if exists) and add members
        r = client.post(
            "/workspaces",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"slug": "team1", "name": "Team 1"},
        )
        assert r.status_code == 200
        m = client.post(
            "/workspaces/team1/members",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"user_id": "alice", "role": "editor"},
        )
        assert m.status_code == 200
        lst = client.get(
            "/workspaces/team1/members",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert lst.status_code == 200 and any(
            x["user_id"] == "alice" for x in lst.json()["members"]
        )

        # Editor writes some memory and a doc
        mm = client.post(
            "/memory",
            headers={"Authorization": f"Bearer {editor_key}", "X-User": "alice"},
            json={"text": "alpha doc text", "key": "fact:test"},
        )
        assert mm.status_code == 200
        rd = client.post(
            "/rag/docs",
            headers={"Authorization": f"Bearer {editor_key}", "X-User": "alice"},
            json={"title": "alpha", "text": "alpha corpus text"},
        )
        assert rd.status_code == 200

        # Audit shows contributions
        au = client.get(
            "/audit/contributions",
            headers={"Authorization": f"Bearer {admin_key}", "X-Workspace": "team1"},
        )
        assert au.status_code == 200
        body = au.json()
        assert body["workspace"] == "team1"
        assert any(it["n"] >= 1 for it in body["memory"]) or any(
            it["n"] >= 1 for it in body["corpus"]
        )

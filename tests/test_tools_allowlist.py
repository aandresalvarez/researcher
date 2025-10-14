from pathlib import Path

from fastapi.testclient import TestClient
import json

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.security.auth import APIKeyRecord


def _setup(tmp_path):
    db = tmp_path / "index.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_table_query_blocked_by_tools_allowlist(tmp_path, monkeypatch):
    # Prepare index DB and app
    db = _setup(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_AUTO_INGEST", "0")
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    # Policy dir with tools_allowed
    pol_dir = tmp_path / "policies"
    pol_dir.mkdir()
    (pol_dir / "no_table.yaml").write_text("tools_allowed:\n  - MATH_EVAL\n", encoding="utf-8")
    monkeypatch.setenv("UAMM_POLICIES_DIR", str(pol_dir))

    app = create_app()

    admin_key = "wk_admin"
    editor_key = "wk_editor"

    def fake_lookup_key(db_path: str, token: str):
        if token == admin_key:
            return APIKeyRecord(
                id="1", workspace="team1", key_hash="h1", role="admin", label="adm", active=True, created=0.0
            )
        if token == editor_key:
            return APIKeyRecord(
                id="2", workspace="team1", key_hash="h2", role="editor", label="ed", active=True, created=0.0
            )
        return None

    from uamm.security import auth as auth_mod

    monkeypatch.setattr(auth_mod, "lookup_key", fake_lookup_key)

    with TestClient(app) as client:
        # Create workspace
        r = client.post(
            "/workspaces",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"slug": "team1", "name": "Team 1"},
        )
        assert r.status_code == 200

        # Apply policy that allows no TABLE_QUERY
        ap = client.post(
            "/workspaces/team1/policies/apply",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"name": "no_table"},
        )
        assert ap.status_code == 200

        # Editor attempts table query (should be blocked by tools_allowed)
        tq = client.post(
            "/table/query",
            headers={"Authorization": f"Bearer {editor_key}"},
            json={"sql": "select 1 as x", "limit": 1},
        )
        assert tq.status_code == 403
        body = tq.json()
        assert body.get("code") in {"tool_forbidden", "table_forbidden"}


# Note: We avoid an SSE-based agent blocking test due to variability in when refinements run.

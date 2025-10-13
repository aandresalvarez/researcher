import importlib
import logging
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api_app(tmp_path, monkeypatch):
    db_path = tmp_path / "redaction.sqlite"
    schema_path = Path("src/uamm/memory/schema.sql")
    monkeypatch.setenv("UAMM_DB_PATH", str(db_path))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", str(schema_path))
    # ensure settings pick up the patched environment
    sys.modules.pop("uamm.config.settings", None)
    sys.modules.pop("uamm.api.main", None)
    create_app = importlib.import_module("uamm.api.main").create_app
    app = create_app()
    with TestClient(app) as client:
        yield client, str(db_path)


def test_answer_persists_redacted_and_logs_safely(api_app, caplog):
    client, db_path = api_app
    pii_question = "Please email me at john.doe@example.com for details."
    with caplog.at_level(logging.INFO, logger="uamm.step"):
        response = client.post(
            "/agent/answer",
            json={
                "question": pii_question,
                "stream": False,
                "memory_budget": 0,
                "max_refinements": 0,
            },
        )
    assert response.status_code == 200
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT question, answer FROM steps LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    stored_question = row[0]
    assert "[REDACTED_EMAIL]" in stored_question
    assert "example.com" not in stored_question
    # structured step log should not leak the raw address
    assert "john.doe@example.com" not in caplog.text

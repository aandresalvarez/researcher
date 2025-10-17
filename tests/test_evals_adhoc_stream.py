from fastapi.testclient import TestClient

from uamm.api.main import create_app


def collect_events(stream_text: str):
    events = []
    current = None
    for line in stream_text.splitlines():
        if line.startswith("event:"):
            current = {"event": line.split(":", 1)[1].strip()}
        elif line.startswith("data:") and current is not None:
            current["data"] = line.split(":", 1)[1].strip()
            events.append(current)
            current = None
    return events


def test_evals_adhoc_stream_basic(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "adhoc_stream.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        import json

        items = [
            {"question": "What is SNNE?", "correct": True, "domain": "default"},
            {
                "question": "Provide a metric sample.",
                "correct": False,
                "domain": "analytics",
            },
        ]
        q = json.dumps(items)
        response = client.get(
            "/evals/run/adhoc/stream",
            params={
                "items": q,
                "cp_enabled": False,
                "max_refinements": 0,
                "tool_budget_per_turn": 0,
                "tool_budget_per_refinement": 0,
            },
        )
    assert response.status_code == 200
    events = collect_events(response.content.decode("utf-8"))
    kinds = [e["event"] for e in events]
    assert "ready" in kinds and "final" in kinds

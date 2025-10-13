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


def test_streaming_returns_final_event(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "stream.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    monkeypatch.setenv("UAMM_ENV_FILE", "tests/data/test.env")
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/agent/answer/stream",
            json={
                "question": "Summarise modular memory",
                "stream": True,
                "use_memory": False,
                "max_refinements": 0,
                "borderline_delta": 0.05,
            },
        )
    assert response.status_code == 200
    events = collect_events(response.content.decode("utf-8"))
    types = [evt["event"] for evt in events]
    assert "ready" in types
    assert "final" in types
    final_payload = [evt["data"] for evt in events if evt["event"] == "final"]
    assert final_payload, "final event payload missing"

from fastapi.testclient import TestClient
from uamm.api.main import create_app


def test_table_query_domain_rbac_forbidden():
    app = create_app()
    with TestClient(app) as client:
        # Configure per-domain allowed tables after startup
        app.state.settings.table_allowed_by_domain = {"biomed": []}
        r = client.post(
            "/table/query",
            json={"sql": "select * from demo", "limit": 1, "domain": "biomed"},
        )
        # Not allowed for domain biomed
        assert r.status_code == 403

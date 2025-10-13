from uamm.gov.executor import evaluate_dag


def test_gov_executor_pass():
    dag = {
        "nodes": [
            {"id": "premise1", "type": "premise", "pcn": "tok1"},
            {"id": "claim1", "type": "claim"},
        ],
        "edges": [{"from": "premise1", "to": "claim1"}],
    }

    def resolver(token: str) -> str:
        return "verified" if token == "tok1" else None

    ok, failing = evaluate_dag(dag, pcn_status=resolver)
    assert ok
    assert failing == []


def test_gov_executor_fails_when_pcn_unverified():
    dag = {
        "nodes": [
            {"id": "premise1", "type": "premise", "pcn": "tok-missing"},
            {"id": "claim1", "type": "claim"},
        ],
        "edges": [{"from": "premise1", "to": "claim1"}],
    }
    ok, failing = evaluate_dag(dag, pcn_status=lambda _: None)
    assert not ok
    assert "pcn_failure:premise1" in failing

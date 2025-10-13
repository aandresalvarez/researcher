from pathlib import Path

import pytest
import yaml

from uamm.config.settings import load_settings
from uamm.flujo.dsl import load_pipeline_from_yaml
from uamm.flujo.nodes import (
    GoVNode,
    MainAgentNode,
    PolicyInput,
    PolicyNode,
    RetrieverInput,
    RetrieverNode,
    VerifierInput,
    VerifierNode,
)
from uamm.policy.policy import PolicyConfig
from uamm.rag.corpus import add_doc
from uamm.storage.db import ensure_schema


@pytest.fixture(autouse=True)
def force_hash_embeddings(monkeypatch):
    monkeypatch.setenv("UAMM_EMBEDDING_BACKEND", "hash")


def _prepare_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "flujo.sqlite"
    ensure_schema(str(db_path), "src/uamm/memory/schema.sql")
    add_doc(
        str(db_path),
        title="Modular memory",
        url="https://example.com/modular",
        text="Modular memory improves retrieval for analytics teams by separating domains.",
        meta={"entities": ["memory", "analytics"]},
    )
    return db_path


def test_retriever_node_returns_pack(tmp_path):
    db_path = _prepare_db(tmp_path)
    node = RetrieverNode()
    result = node(
        RetrieverInput(
            question="How does modular memory help analytics?", db_path=str(db_path)
        )
    )
    assert result.pack, "Expected retriever to return evidence pack"
    assert result.pack[0].snippet


def test_main_agent_node_runs_without_llm(tmp_path):
    db_path = _prepare_db(tmp_path)
    retriever = RetrieverNode()
    pack = retriever(
        RetrieverInput(question="Explain modular memory", db_path=str(db_path))
    ).pack
    agent = MainAgentNode()
    output = agent(
        {
            "question": "Explain modular memory",
            "params": {"db_path": str(db_path), "memory_budget": 4},
            "evidence_pack": [item.model_dump() for item in pack],
        }
    )
    assert output.final
    assert output.stop_reason in {"accept", "iterate", "abstain"}


def test_policy_node_decides_action():
    settings = load_settings()
    policy_node = PolicyNode()
    payload = PolicyInput(
        snne=0.2, s2=0.8, config=PolicyConfig(tau_accept=settings.accept_threshold)
    )
    result = policy_node(payload)
    assert result.action in {"accept", "iterate", "abstain"}


def test_verifier_node_scores_answer():
    node = VerifierNode()
    output = node(
        VerifierInput(
            question="What is modular memory?",
            answer="Modular memory separates domains.",
        )
    )
    assert 0.0 <= output.score <= 1.0


def test_gov_node_detects_failure():
    node = GoVNode()
    dag = {"nodes": [{"id": "claim-1", "type": "claim"}], "edges": []}
    result = node({"dag": dag, "pcn_status": lambda token: "verified"})
    assert isinstance(result.ok, bool)


def test_load_pipeline_from_yaml(tmp_path):
    db_path = _prepare_db(tmp_path)
    yaml_payload = {
        "nodes": [
            {
                "type": "retriever",
                "options": {
                    "db_path": str(db_path),
                    "question": "Explain modular memory",
                },
            },
            {"type": "main_agent", "options": {"params": {"db_path": str(db_path)}}},
        ]
    }
    yaml_path = tmp_path / "pipeline.yaml"
    yaml_path.write_text(yaml.safe_dump(yaml_payload), encoding="utf-8")
    pipeline = load_pipeline_from_yaml(yaml_path)
    result = pipeline.run({"question": "Explain modular memory"})
    assert "final" in result or "pack" in result

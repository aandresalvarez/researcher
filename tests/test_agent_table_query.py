import sqlite3
from importlib.resources import files

from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig
from uamm.storage.db import ensure_schema


def _setup_demo_db(tmp_path):
    db_path = tmp_path / "agent_table.sqlite"
    schema_path = files("uamm.memory").joinpath("schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, cohort TEXT)")
        conn.executemany(
            "INSERT INTO demo (cohort) VALUES (?)", [("a",), ("a",), ("b",)]
        )
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


def test_agent_runs_table_query_for_missing_table_data(tmp_path):
    db_path = _setup_demo_db(tmp_path)
    agent = MainAgent(cp_enabled=False, policy=PolicyConfig(delta=1.0))
    params = {
        "question": "How many rows are in the demo table?",
        "max_refinements": 1,
        "tool_budget_per_refinement": 3,
        "tool_budget_per_turn": 3,
        "memory_budget": 0,
        "db_path": db_path,
    }
    result = agent.answer(params=params)
    final_text = result["final"]
    assert "3" in final_text
    trace = result["trace"][-1]
    assert "TABLE_QUERY" in trace["tools_used"]

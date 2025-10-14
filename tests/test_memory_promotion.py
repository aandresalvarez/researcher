from uamm.memory.promote import promote_episodic_to_semantic
from uamm.storage.memory import add_memory


def test_memory_promotion_runs(tmp_path):
    db = tmp_path / "mem.sqlite"
    # Initialize schema
    from uamm.storage.db import ensure_schema

    ensure_schema(str(db), "src/uamm/memory/schema.sql")
    # Add repeated episodic facts
    for _ in range(3):
        add_memory(str(db), key="episodic:", text="The system cached answers.")
    stats = promote_episodic_to_semantic(str(db), min_support=3)
    assert stats.promoted >= 1


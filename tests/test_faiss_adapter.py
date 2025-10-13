import numpy as np

from uamm.rag.faiss_adapter import FaissAdapter


def test_faiss_adapter_numpy_fallback():
    adapter = FaissAdapter(dim=4)
    adapter.add("a", np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    adapter.add("b", np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32))

    hits = adapter.search(np.array([1.0, 0.1, 0.0, 0.0], dtype=np.float32), k=2)
    assert hits, "expected fallback adapter to return hits"
    assert hits[0].doc_id == "a"
    assert 0.0 <= hits[0].score <= 1.0
    assert hits[0].score >= hits[-1].score

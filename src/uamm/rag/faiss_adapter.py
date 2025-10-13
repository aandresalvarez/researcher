from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import numpy as np


class FaissAdapterError(RuntimeError):
    """Raised when FAISS cannot be used and fallback is disabled."""


@dataclass
class VectorHit:
    doc_id: str
    score: float


def _normalise(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if norm > 0:
        v = v / norm
    return v


class FaissAdapter:
    """Optional FAISS-backed vector index with numpy fallback.

    When FAISS is unavailable the adapter transparently falls back to
    an in-memory numpy implementation so tests can exercise parity logic.
    """

    def __init__(
        self,
        dim: int,
        *,
        metric: str = "cosine",
        require_faiss: bool = False,
    ) -> None:
        if metric != "cosine":
            raise ValueError("only cosine metric is supported")
        self._dim = int(dim)
        self._metric = metric
        self._ids: List[str] = []
        self._meta: Dict[str, Dict] = {}
        self._vectors: List[np.ndarray] = []
        self._use_faiss = False
        self._faiss = None
        self._index = None
        try:
            import faiss  # type: ignore

            self._faiss = faiss
            # Use inner product (requires normalised vectors for cosine equivalence).
            self._index = faiss.IndexFlatIP(self._dim)
            self._use_faiss = True
        except ImportError:
            if require_faiss:
                raise FaissAdapterError("faiss module is not available") from None
            # numpy fallback is already set up via _vectors

    @property
    def uses_faiss(self) -> bool:
        return self._use_faiss

    def add(self, doc_id: str, vector: np.ndarray, *, meta: Dict | None = None) -> None:
        if vector.shape[0] != self._dim:
            raise ValueError(
                f"expected vector dim {self._dim}, received {vector.shape[0]}"
            )
        normalised = _normalise(vector)
        if self._use_faiss and self._index is not None and self._faiss is not None:
            prepared = normalised.reshape(1, -1).astype(np.float32)
            self._faiss.normalize_L2(prepared)
            self._index.add(prepared)
        else:
            self._vectors.append(normalised)
        self._ids.append(doc_id)
        if meta:
            self._meta[doc_id] = meta

    def bulk_add(self, items: Iterable[Tuple[str, np.ndarray, Dict | None]]) -> None:
        for doc_id, vector, meta in items:
            self.add(doc_id, vector, meta=meta)

    def search(self, query: np.ndarray, k: int = 5) -> List[VectorHit]:
        if not self._ids:
            return []
        if query.shape[0] != self._dim:
            raise ValueError(
                f"expected query dim {self._dim}, received {query.shape[0]}"
            )
        normalised_query = _normalise(query).reshape(1, -1)

        if self._use_faiss and self._index is not None and self._faiss is not None:
            self._faiss.normalize_L2(normalised_query)
            scores, idxs = self._index.search(
                normalised_query.astype(np.float32), min(k, len(self._ids))
            )
            hits: List[VectorHit] = []
            for score, idx in zip(scores[0], idxs[0]):
                if idx == -1:
                    continue
                doc_id = self._ids[idx]
                hits.append(VectorHit(doc_id=doc_id, score=_to_unit_interval(score)))
            return hits

        # numpy fallback
        sims: List[Tuple[str, float]] = []
        q_vec = normalised_query.ravel()
        for doc_id, vec in zip(self._ids, self._vectors):
            sims.append((doc_id, float(np.dot(vec, q_vec))))
        sims.sort(key=lambda item: item[1], reverse=True)
        return [
            VectorHit(doc_id=doc_id, score=_to_unit_interval(score))
            for doc_id, score in sims[:k]
        ]

    def get_meta(self, doc_id: str) -> Dict:
        return self._meta.get(doc_id, {})

    def __len__(self) -> int:
        return len(self._ids)


def _to_unit_interval(score: float) -> float:
    # Convert cosine similarity [-1, 1] into [0, 1]
    return float(max(0.0, min(1.0, (score + 1.0) / 2.0)))

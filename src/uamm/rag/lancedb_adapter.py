from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import numpy as np

_LOGGER = logging.getLogger("uamm.rag.lancedb")


class LanceDBUnavailable(RuntimeError):
    """Raised when LanceDB optional dependency is not installed."""


@dataclass
class LanceDBHit:
    doc_id: str
    score: float


class LanceDBAdapter:
    """LanceDB-backed vector index (PRD §6.2, §15 M2)."""

    def __init__(
        self,
        *,
        dim: int,
        uri: str,
        table: str,
        metric: str = "cosine",
    ) -> None:
        try:
            import lancedb  # type: ignore
            from lancedb import vector  # type: ignore
            import pyarrow as pa  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency missing
            raise LanceDBUnavailable("lancedb is not installed") from exc

        self._dim = int(dim)
        self._metric = metric
        self._uri = uri
        self._table_name = table
        self._lancedb = lancedb.connect(uri)

        schema = pa.schema(
            [
                pa.field("doc_id", pa.string()),
                pa.field("vector", vector(self._dim)),
                pa.field("meta", pa.map_(pa.string(), pa.string())).with_nullable(True),
            ]
        )

        table_names = set(self._lancedb.table_names())
        if table in table_names:
            self._table = self._lancedb.open_table(table)
            vec_field = next(
                (field for field in self._table.schema if field.name == "vector"), None
            )
            if vec_field is not None:
                actual_dim = getattr(vec_field.type, "list_size", None) or getattr(
                    vec_field.type, "dimension", None
                )
                if actual_dim and int(actual_dim) != self._dim:
                    raise ValueError(
                        f"LanceDB table '{table}' expects vector dim {actual_dim}, got {self._dim}"
                    )
        else:
            self._table = self._lancedb.create_table(table, schema=schema)

    @property
    def table_name(self) -> str:
        return self._table_name

    @property
    def uri(self) -> str:
        return self._uri

    def _prepare_vector(self, vec: np.ndarray) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float32)
        if arr.ndim != 1:
            arr = arr.reshape(-1)
        if arr.shape[0] != self._dim:
            raise ValueError(
                f"expected vector dim {self._dim}, received {arr.shape[0]}"
            )
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return arr

    def upsert(self, doc_id: str, vector: np.ndarray, meta: Dict | None = None) -> None:
        self.bulk_add([(doc_id, vector, meta)])

    def bulk_add(self, items: Iterable[Tuple[str, np.ndarray, Dict | None]]) -> None:
        batch: List[Dict[str, object]] = []
        for entry in items:
            if not entry:
                continue
            if len(entry) == 2:
                doc_id, vector = entry  # type: ignore[misc]
                meta = None
            else:
                doc_id, vector, meta = entry  # type: ignore[misc]
            if not doc_id:
                continue
            prepared = self._prepare_vector(vector)
            payload = {
                "doc_id": str(doc_id),
                "vector": prepared.tolist(),
                "meta": {str(k): str(v) for k, v in (meta or {}).items()},
            }
            batch.append(payload)
        if not batch:
            return
        try:
            self._table.add(batch)
        except Exception as exc:  # pragma: no cover - LanceDB internal failure
            _LOGGER.warning(
                "lancedb_add_failed",
                extra={"error": str(exc), "batch_size": len(batch)},
            )
            raise

    def search(self, query: np.ndarray, k: int = 5) -> List[LanceDBHit]:
        if k <= 0:
            return []
        if self._table.count_rows() == 0:
            return []
        q_vec = self._prepare_vector(query)
        try:
            records = (
                self._table.search(q_vec.tolist())
                .metric(self._metric)
                .limit(k)
                .to_list()
            )
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning("lancedb_search_failed", extra={"error": str(exc)})
            return []
        hits: List[LanceDBHit] = []
        for row in records:
            doc_id = row.get("doc_id")
            if not doc_id:
                continue
            raw_score = row.get("score")
            if raw_score is None:
                raw_score = row.get("_distance") or row.get("distance")
                if raw_score is None:
                    continue
                similarity = 1.0 - float(raw_score)
            else:
                similarity = float(raw_score)
                if self._metric == "cosine" and similarity > 1.5:
                    similarity = 1.0 - similarity
            hits.append(
                LanceDBHit(doc_id=str(doc_id), score=_to_unit_interval(similarity))
            )
        return hits


def _to_unit_interval(score: float) -> float:
    return float(max(0.0, min(1.0, (score + 1.0) / 2.0)))

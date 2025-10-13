from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Tuple, Optional
from dataclasses import dataclass

import numpy as np

from uamm.storage.memory import search_memory
from uamm.rag.corpus import fetch_docs_by_ids, search_docs
from uamm.rag.embeddings import embed_text, cosine
from uamm.rag.faiss_adapter import FaissAdapter
from uamm.rag.vector_store import LanceDBUnavailable, lancedb_search

_LOGGER = logging.getLogger("uamm.rag.retriever")


@dataclass
class RetrieverConfig:
    memory_k: int = 8
    vector_backend: str = "none"
    corpus_k: int = 8
    budget: int = 8
    min_score: float = 0.1
    w_sparse: float = 0.5
    w_dense: float = 0.5
    use_faiss: bool = False
    kg_boost: float = 0.15
    lancedb_uri: str | None = None
    lancedb_table: str = "rag_vectors"
    lancedb_metric: str = "cosine"
    lancedb_k: Optional[int] = None


class EvidenceItem(Dict[str, Any]):
    """Typed dict-style container for evidence hits."""


def _normalise_weights(w_sparse: float, w_dense: float) -> Tuple[float, float]:
    ws = max(0.0, w_sparse)
    wd = max(0.0, w_dense)
    total = ws + wd
    if total <= 0:
        return 1.0, 0.0
    return ws / total, wd / total


def _dense_score_from_vec(question_vec: np.ndarray, snippet_vec: np.ndarray) -> float:
    if question_vec.shape != snippet_vec.shape:
        m = min(question_vec.shape[0], snippet_vec.shape[0])
        question_vec = question_vec[:m]
        snippet_vec = snippet_vec[:m]
    dense = cosine(question_vec, snippet_vec)
    dense = (dense + 1.0) / 2.0
    return float(max(0.0, min(1.0, dense)))


def _prepare_candidates(
    memory_hits: Iterable[Dict[str, Any]],
    corpus_hits: Iterable[Dict[str, Any]],
) -> List[EvidenceItem]:
    candidates: List[EvidenceItem] = []
    for hit in memory_hits:
        item: EvidenceItem = EvidenceItem(
            id=str(hit.get("id")),
            snippet=str(hit.get("snippet") or ""),
            why=str(hit.get("why") or "memory"),
            sparse_score=float(hit.get("score") or 0.0),
            source="memory",
        )
        if hit.get("meta"):
            item["meta"] = dict(hit.get("meta"))
        candidates.append(item)
    for hit in corpus_hits:
        item = EvidenceItem(
            id=str(hit.get("id")),
            snippet=str(hit.get("snippet") or ""),
            why=str(hit.get("why") or "corpus"),
            sparse_score=float(hit.get("score") or 0.0),
            source="corpus",
        )
        if hit.get("url"):
            item["url"] = hit["url"]
        if hit.get("title"):
            item["title"] = hit["title"]
        if hit.get("meta"):
            item["meta"] = dict(hit.get("meta"))
        candidates.append(item)
    return candidates


def _prefer_candidate(new: EvidenceItem, existing: EvidenceItem) -> bool:
    if new.get("url") and not existing.get("url"):
        return True
    if new.get("source") == "corpus" and existing.get("source") != "corpus":
        return True
    return False


def _snippet_key(snippet: str) -> str:
    s = (snippet or "").strip().lower()
    if ":" in s[:60]:
        s = s.split(":", 1)[1].strip()
    if len(s) > 200:
        s = s[-200:]
    return s


def _tokenize(text: str) -> List[str]:
    return [tok for tok in re.split(r"\W+", text.lower()) if tok]


def _entities_from_meta(meta: Dict[str, Any] | None) -> List[str]:
    if not meta:
        return []
    entities = meta.get("entities")
    if not entities:
        return []
    if isinstance(entities, str):
        try:
            return [ent.strip().lower() for ent in entities.split("|") if ent.strip()]
        except Exception:
            return [entities.lower()]
    if isinstance(entities, (list, tuple, set)):
        out: List[str] = []
        for ent in entities:
            if isinstance(ent, str) and ent.strip():
                out.append(ent.strip().lower())
        return out
    return []


def _kg_bonus(
    query_terms: List[str], meta: Dict[str, Any] | None, weight: float
) -> float:
    if weight <= 0:
        return 0.0
    entities = _entities_from_meta(meta)
    if not entities:
        return 0.0
    qt = set(query_terms)
    matches = sum(1 for ent in entities if ent in qt)
    if matches <= 0:
        return 0.0
    return min(weight * matches, weight * 3)


def retrieve(
    query: str,
    *,
    db_path: str | None,
    config: RetrieverConfig | None = None,
) -> List[EvidenceItem]:
    """Hybrid retrieval: merge memory + corpus with sparse/dense scoring (PRD §7.7)."""
    if not db_path:
        return []
    cfg = config or RetrieverConfig()
    try:
        memory_hits = search_memory(db_path, query, k=cfg.memory_k)
    except Exception:
        memory_hits = []
    try:
        corpus_hits = search_docs(db_path, query, k=cfg.corpus_k)
    except Exception:
        corpus_hits = []
    if not memory_hits and not corpus_hits:
        return []
    candidates = _prepare_candidates(memory_hits, corpus_hits)
    if not candidates:
        return []
    q_vec = embed_text(query)
    if (
        cfg.vector_backend
        and cfg.vector_backend.lower() == "lancedb"
        and cfg.lancedb_uri
    ):
        try:
            lancedb_results = lancedb_search(
                q_vec,
                uri=cfg.lancedb_uri,
                table=cfg.lancedb_table,
                metric=cfg.lancedb_metric,
                k=cfg.lancedb_k or cfg.corpus_k or 8,
            )
            if lancedb_results:
                doc_map = fetch_docs_by_ids(
                    db_path, [hit.doc_id for hit in lancedb_results]
                )
                for hit in lancedb_results:
                    doc = doc_map.get(hit.doc_id)
                    if not doc:
                        continue
                    item: EvidenceItem = EvidenceItem(
                        id=hit.doc_id,
                        snippet=doc.get("snippet", ""),
                        why="lancedb match",
                        sparse_score=float(hit.score),
                        source="corpus",
                        dense_score=float(hit.score),
                        score=float(hit.score),
                    )
                    if doc.get("url"):
                        item["url"] = doc["url"]
                    if doc.get("title"):
                        item["title"] = doc["title"]
                    if doc.get("meta"):
                        item["meta"] = doc["meta"]
                    candidates.append(item)
        except LanceDBUnavailable:
            _LOGGER.warning("lancedb_unavailable", extra={"uri": cfg.lancedb_uri})
        except Exception as exc:  # pragma: no cover - best effort integration
            _LOGGER.warning("lancedb_search_error", extra={"error": str(exc)})
    ws, wd = _normalise_weights(cfg.w_sparse, cfg.w_dense)
    query_terms = _tokenize(query)
    dedup: Dict[str, EvidenceItem] = {}
    for candidate in candidates:
        snippet = candidate.get("snippet", "")
        snippet_vec = embed_text(snippet)
        dense = _dense_score_from_vec(q_vec, snippet_vec)
        sparse = max(0.0, min(1.0, float(candidate.get("sparse_score", 0.0))))
        hybrid = (ws * sparse) + (wd * dense)
        kg = _kg_bonus(
            query_terms,
            candidate.get("meta") if isinstance(candidate.get("meta"), dict) else None,
            cfg.kg_boost,
        )
        total_score = hybrid + kg
        if hybrid < cfg.min_score:
            continue
        candidate["_snippet_vec"] = snippet_vec
        candidate["dense_score"] = dense
        candidate["score"] = total_score
        if kg > 0:
            candidate["kg_bonus"] = kg
        key = _snippet_key(snippet)
        existing = dedup.get(key)
        if existing is None:
            dedup[key] = candidate
            continue
        existing_score = float(existing.get("score", 0.0))
        if total_score > existing_score + 1e-9:
            dedup[key] = candidate
            continue
        if abs(total_score - existing_score) <= 1e-9 and _prefer_candidate(
            candidate, existing
        ):
            dedup[key] = candidate
            continue
    ranked = sorted(dedup.values(), key=lambda x: x.get("score", 0.0), reverse=True)
    if not ranked:
        return []

    adapter: Optional[FaissAdapter] = None
    if cfg.use_faiss:
        try:
            adapter = FaissAdapter(dim=q_vec.shape[0])
        except Exception:
            adapter = None

    if adapter is not None:
        for candidate in ranked:
            snippet_vec = candidate.get("_snippet_vec")
            if isinstance(snippet_vec, np.ndarray):
                try:
                    adapter.add(candidate["id"], snippet_vec)
                except Exception:
                    adapter = None
                    break

    dense_overrides: Dict[str, float] = {}
    if adapter is not None:
        hits = adapter.search(q_vec, k=len(ranked))
        dense_overrides = {hit.doc_id: hit.score for hit in hits}

    filtered: List[EvidenceItem] = []
    for candidate in ranked:
        candidate.pop("_snippet_vec", None)
        override = dense_overrides.get(candidate["id"])
        if override is not None:
            candidate["dense_score"] = override
        sparse = max(0.0, min(1.0, float(candidate.get("sparse_score", 0.0))))
        dense = float(candidate.get("dense_score", 0.0))
        hybrid = (ws * sparse) + (wd * dense)
        kg = float(candidate.get("kg_bonus", 0.0))
        total = hybrid + kg
        if total < cfg.min_score:
            continue
        candidate["score"] = total
        filtered.append(candidate)

    if cfg.budget > 0:
        filtered = filtered[: cfg.budget]
    return filtered

import logging
import os
from functools import lru_cache

import numpy as np


_LOGGER = logging.getLogger("uamm.embeddings")
_DEFAULT_BACKEND = os.getenv("UAMM_EMBEDDING_BACKEND", "openai").lower()
_OPENAI_MODEL = os.getenv("UAMM_EMBEDDING_MODEL", "text-embedding-3-small")
_OPENAI_DIM = int(os.getenv("UAMM_OPENAI_EMBED_DIM", "1536"))
_HASH_DIM = int(os.getenv("UAMM_HASH_EMBED_DIM", "384"))
_OPENAI_CLIENT = None


def _normalise(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec


@lru_cache(maxsize=8192)
def embed_text(text: str, dim: int = None) -> np.ndarray:
    """Return a normalized embedding for the given text.

    Attempts to use OpenAI embeddings when available, with a deterministic hashing fallback.
    """
    clean = (text or "").strip()
    if not clean:
        backend = _DEFAULT_BACKEND
        if backend == "openai":
            target_dim = _OPENAI_DIM
        else:
            target_dim = dim or _HASH_DIM
        return np.zeros(target_dim, dtype=np.float32)
    backend = _DEFAULT_BACKEND
    if backend == "openai":
        try:
            return _embed_openai(clean)
        except Exception as exc:  # pragma: no cover - network/credential issues
            _LOGGER.warning("embed_openai_failed", extra={"error": str(exc)})
            backend = "hash"
    target_dim = dim or _HASH_DIM
    return _hash_embedding(clean, target_dim)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        m = min(a.shape[0], b.shape[0])
        a = a[:m]
        b = b[:m]
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _embed_openai(text: str) -> np.ndarray:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - dependency missing
            raise RuntimeError(f"OpenAI client unavailable: {exc}") from exc
        _OPENAI_CLIENT = OpenAI()
    response = _OPENAI_CLIENT.embeddings.create(
        model=_OPENAI_MODEL,
        input=text,
    )
    data = response.data[0].embedding
    vec = np.asarray(data, dtype=np.float32)
    return _normalise(vec)


def _hash_embedding(text: str, dim: int) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    for tok in text.lower().split():
        h = hash(tok) % dim
        vec[h] += 1.0
    return _normalise(vec)

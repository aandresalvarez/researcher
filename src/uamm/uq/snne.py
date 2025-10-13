from typing import Callable, List
import math
import numpy as np


def snne(answers: List[str], tau: float, embed: Callable[[str], np.ndarray]) -> float:
    """Compute SNNE raw score. Normalization to [0,1] occurs downstream (PRD §7.2)."""
    if not answers:
        return 0.0
    V = np.stack([embed(a) for a in answers])
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    S = V @ V.T
    lse = np.log(np.sum(np.exp(S / max(tau, 1e-6)), axis=1))
    return float(-np.mean(lse))


def normalize(raw: float) -> float:
    """Map raw SNNE values (typically ≤0) into [0,1] via logistic squashing."""
    if math.isnan(raw):
        return 1.0
    # raw is usually negative; logistic keeps ordering while bounding
    value = 1.0 / (1.0 + math.exp(raw))
    return float(min(1.0, max(0.0, value)))

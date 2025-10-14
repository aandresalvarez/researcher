"""Verification utilities (faithfulness, attribution, etc.)."""

__all__ = [
    "extract_claims",
    "align_claims_to_evidence",
    "compute_faithfulness",
]

from .faithfulness import (
    extract_claims,
    align_claims_to_evidence,
    compute_faithfulness,
)


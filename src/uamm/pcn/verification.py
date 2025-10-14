from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

from uamm.tools.math_eval import math_eval
from uamm.pcn.units import validate_numeric_unit


class VerificationError(RuntimeError):
    """Raised when a PCN entry fails verification."""


@dataclass
class PCNEntry:
    token_id: str
    policy: Dict
    provenance: Dict
    status: str = "pending"  # pending|verified|failed
    value: Optional[str] = None
    reason: Optional[str] = None

    def pending_event(self) -> Dict:
        return {
            "type": "pcn_pending",
            "id": self.token_id,
            "policy": self.policy,
            "provenance": self.provenance,
        }

    def verified_event(self) -> Dict:
        return {
            "type": "pcn_verified",
            "id": self.token_id,
            "value": self.value,
            "policy": self.policy,
            "provenance": self.provenance,
        }

    def failed_event(self) -> Dict:
        return {
            "type": "pcn_failed",
            "id": self.token_id,
            "reason": self.reason,
            "policy": self.policy,
            "provenance": self.provenance,
        }


class PCNVerifier:
    """Proof-Carrying Numbers verifier with math/sql/url helpers."""

    def __init__(self) -> None:
        self._entries: Dict[str, PCNEntry] = {}

    # Registration -----------------------------------------------------

    def register(
        self,
        token_id: str,
        *,
        policy: Optional[Dict] = None,
        provenance: Optional[Dict] = None,
    ) -> Dict:
        entry = PCNEntry(
            token_id=token_id,
            policy=dict(policy or {}),
            provenance=dict(provenance or {}),
        )
        self._entries[token_id] = entry
        return entry.pending_event()

    # Verification helpers --------------------------------------------

    def verify_math(
        self, token_id: str, *, expr: str, observed_value: float | int | str
    ) -> Dict:
        entry = self._require_entry(token_id)
        try:
            expected = float(math_eval(expr))
            value = float(observed_value)
            tolerance = float(entry.policy.get("tolerance", 0.0) or 0.0)
            if math.isfinite(tolerance) and abs(expected - value) > tolerance:
                raise VerificationError(
                    f"value {value} differs from expected {expected} (tol={tolerance})"
                )
            # Optional unit validation
            units = (
                entry.policy.get("units") if isinstance(entry.policy, dict) else None
            )
            if units:
                if not validate_numeric_unit(value, str(units)):
                    raise VerificationError(f"invalid_units:{units}")
        except Exception as exc:
            entry.status = "failed"
            entry.reason = str(exc)
            return entry.failed_event()
        entry.status = "verified"
        entry.value = _format_number(value)
        return entry.verified_event()

    def verify_sql(self, token_id: str, *, value: float | int | str) -> Dict:
        entry = self._require_entry(token_id)
        try:
            numeric = float(value)
            # Optional unit validation
            units = (
                entry.policy.get("units") if isinstance(entry.policy, dict) else None
            )
            if units:
                if not validate_numeric_unit(numeric, str(units)):
                    raise VerificationError(f"invalid_units:{units}")
        except Exception as exc:
            entry.status = "failed"
            entry.reason = f"not numeric: {exc}"
            return entry.failed_event()
        entry.status = "verified"
        entry.value = _format_number(numeric)
        return entry.verified_event()

    def verify_url(self, token_id: str, *, url: str) -> Dict:
        entry = self._require_entry(token_id)
        entry.status = "verified"
        entry.value = url
        return entry.verified_event()

    def fail(self, token_id: str, reason: str) -> Dict:
        entry = self._require_entry(token_id)
        entry.status = "failed"
        entry.reason = reason
        return entry.failed_event()

    # Introspection ----------------------------------------------------

    def value_for(self, token_id: str) -> Optional[str]:
        entry = self._entries.get(token_id)
        if not entry or entry.status != "verified":
            return None
        return entry.value

    def status_for(self, token_id: str) -> Optional[str]:
        entry = self._entries.get(token_id)
        return entry.status if entry else None

    # Internal ---------------------------------------------------------

    def _require_entry(self, token_id: str) -> PCNEntry:
        entry = self._entries.get(token_id)
        if entry is None:
            entry = PCNEntry(token_id=token_id, policy={}, provenance={})
            self._entries[token_id] = entry
        return entry


def _format_number(val: float) -> str:
    if float(val).is_integer():
        return str(int(val))
    return f"{val:.6g}"

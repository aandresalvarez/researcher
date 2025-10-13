import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class IdempotencyItem:
    ts: float
    data: Dict[str, Any]


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._store: Dict[str, IdempotencyItem] = {}
        self._ttl = ttl_seconds

    def get(self, key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        item = self._store.get(key)
        if not item:
            return None
        if time.time() - item.ts > self._ttl:
            self._store.pop(key, None)
            return None
        return item.data

    def set(self, key: Optional[str], data: Dict[str, Any]) -> None:
        if not key:
            return
        # prune occasionally
        now = time.time()
        if len(self._store) > 2048:
            expired = [k for k, v in self._store.items() if now - v.ts > self._ttl]
            for k in expired:
                self._store.pop(k, None)
        self._store[key] = IdempotencyItem(ts=now, data=data)


@dataclass
class ApprovalItem:
    ts: float
    status: str  # "pending" | "approved" | "denied"
    context: Dict[str, Any]
    reason: Optional[str] = None


class ApprovalsStore:
    """In-memory approvals store (stub for PRD §10.4).

    This is a lightweight placeholder to support a /tools/approve API.
    Integrating pause/resume semantics in the agent is a later step.
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._store: Dict[str, ApprovalItem] = {}
        self._ttl = ttl_seconds

    def create(self, approval_id: str, context: Dict[str, Any]) -> None:
        now = time.time()
        # prune expired
        expired = [k for k, v in self._store.items() if now - v.ts > self._ttl]
        for k in expired:
            self._store.pop(k, None)
        self._store[approval_id] = ApprovalItem(
            ts=now, status="pending", context=context
        )

    def get(self, approval_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not approval_id:
            return None
        item = self._store.get(approval_id)
        if not item:
            return None
        if time.time() - item.ts > self._ttl:
            self._store.pop(approval_id, None)
            return None
        return {
            "approval_id": approval_id,
            "status": item.status,
            "context": item.context,
            "reason": item.reason,
        }

    def approve(
        self, approval_id: str, approved: bool, reason: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        item = self._store.get(approval_id)
        if not item:
            return None
        item.status = "approved" if approved else "denied"
        item.reason = reason
        return self.get(approval_id)

    def consume(self, approval_id: str) -> Optional[Dict[str, Any]]:
        item = self._store.pop(approval_id, None)
        if not item:
            return None
        return {
            "approval_id": approval_id,
            "status": item.status,
            "context": item.context,
            "reason": item.reason,
        }

    def snapshot(self) -> Dict[str, Any]:
        """Return counts and latency stats for approvals."""
        now = time.time()
        pending = approved = denied = 0
        pending_ages: list[float] = []
        for item in self._store.values():
            age = max(0.0, now - item.ts)
            if item.status == "pending":
                pending += 1
                pending_ages.append(age)
            elif item.status == "approved":
                approved += 1
            elif item.status == "denied":
                denied += 1
        avg_age = (sum(pending_ages) / len(pending_ages)) if pending_ages else 0.0
        max_age = max(pending_ages) if pending_ages else 0.0
        return {
            "pending": pending,
            "approved": approved,
            "denied": denied,
            "avg_pending_age": avg_age,
            "max_pending_age": max_age,
        }


@dataclass
class TunerProposalItem:
    ts: float
    status: str  # "pending" | "approved" | "rejected" | "applied"
    payload: Dict[str, Any]
    reason: Optional[str] = None
    applied_at: Optional[float] = None


class TunerProposalStore:
    """In-memory store for tuner proposals requiring approval."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._store: Dict[str, TunerProposalItem] = {}
        self._ttl = ttl_seconds

    def _prune(self) -> None:
        now = time.time()
        expired = [
            pid for pid, item in self._store.items() if now - item.ts > self._ttl
        ]
        for pid in expired:
            self._store.pop(pid, None)

    def create(self, proposal_id: str, payload: Dict[str, Any]) -> None:
        self._prune()
        self._store[proposal_id] = TunerProposalItem(
            ts=time.time(), status="pending", payload=payload
        )

    def get(self, proposal_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not proposal_id:
            return None
        item = self._store.get(proposal_id)
        if not item:
            return None
        if time.time() - item.ts > self._ttl:
            self._store.pop(proposal_id, None)
            return None
        return {
            "proposal_id": proposal_id,
            "status": item.status,
            "payload": item.payload,
            "reason": item.reason,
            "applied_at": item.applied_at,
        }

    def set_status(
        self, proposal_id: str, status: str, *, reason: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        item = self._store.get(proposal_id)
        if not item:
            return None
        item.status = status
        item.reason = reason
        if status == "applied":
            item.applied_at = time.time()
        return self.get(proposal_id)

    def consume(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        item = self._store.pop(proposal_id, None)
        if not item:
            return None
        return {
            "proposal_id": proposal_id,
            "status": item.status,
            "payload": item.payload,
            "reason": item.reason,
            "applied_at": item.applied_at,
        }


@dataclass
class CPThresholdEntry:
    tau: Optional[float]
    target: float
    stats: Dict[str, Any]
    ts: float


class CPThresholdCache:
    """In-memory cache for CP thresholds per domain/target."""

    def __init__(self) -> None:
        self._cache: Dict[str, CPThresholdEntry] = {}

    def get(self, domain: str, target: float) -> Optional[float]:
        entry = self._cache.get(domain)
        if not entry:
            return None
        if entry.target != target:
            return None
        return entry.tau

    def set(
        self, domain: str, tau: Optional[float], target: float, stats: Dict[str, Any]
    ) -> None:
        self._cache[domain] = CPThresholdEntry(
            tau=tau, target=target, stats=dict(stats), ts=time.time()
        )

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for domain, entry in self._cache.items():
            out[domain] = {
                "tau": entry.tau,
                "target": entry.target,
                "stats": entry.stats,
                "ts": entry.ts,
            }
        return out

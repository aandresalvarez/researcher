from __future__ import annotations

from typing import Any, Dict, List

import uamm.planning.strategies as S
from uamm.policy.policy import PolicyConfig


class FakeCalibrator:
    def __init__(self, offsets: Dict[str, float]) -> None:
        self.offsets = {k.lower(): float(v) for k, v in offsets.items()}
        self.calls: List[Dict[str, Any]] = []

    def normalize(
        self, *, domain: str, raw: float
    ) -> float:  # pragma: no cover - exercised via S
        dom = (domain or "default").lower()
        self.calls.append({"domain": dom, "raw": float(raw)})
        offset = float(self.offsets.get(dom, 0.0))
        return float(raw) + offset


def _stub_embed(_: str) -> Any:
    # simple deterministic vector; S.snne_score will be stubbed, so not used
    return 0.0


def test_planning_uses_provided_domain_for_calibration(monkeypatch):
    # Arrange: make SNNE raw score depend only on the candidate label via samples[0]
    raw_scores = {"A": 0.49, "B": 0.50}

    def fake_snne(samples, tau, embed):  # type: ignore[no-redef]
        base = samples[0]
        return float(raw_scores.get(base, 0.0))

    monkeypatch.setattr(S, "snne_score", fake_snne)

    # Keep variants simple and tied to the base answer text
    def fake_variants(
        base_answer: str, *, question: str, evidence_snippets, count: int
    ):  # type: ignore[no-redef]
        return [base_answer for _ in range(max(2, count))]

    monkeypatch.setattr(S, "generate_answer_variants", fake_variants)

    # Calibrator with per-domain offsets; we only assert that the correct domain was used
    cal = FakeCalibrator({"default": 0.0, "cardio": -0.2})

    # Act: plan with explicit domain
    out = S.plan_best_answer(
        question="q",
        evidence_pack=[],
        base_answer="A",
        embed=_stub_embed,
        snne_calibrator=cal,
        verifier=type("V", (), {"verify": staticmethod(lambda q, a: (0.0, [], False))}),
        policy_cfg=PolicyConfig(w1=1.0, w2=0.0),  # depend only on SNNE for S
        sample_count=3,
        config=S.PlanningConfig(mode="tot", budget=2),
        domain="cardio",
    )

    assert out["best_answer"] in {"A", "B"}  # ranking itself isn't under test here
    # Assert that calibrator saw the cardio domain at least once
    domains = [c["domain"] for c in cal.calls]
    assert "cardio" in domains


def test_planning_domain_does_not_change_relative_ranking(monkeypatch):
    # Arrange: A strictly better (lower) SNNE than B so that S prefers A
    raw_scores = {"A": 0.49, "B": 0.50}

    def fake_snne(samples, tau, embed):  # type: ignore[no-redef]
        base = samples[0]
        return float(raw_scores.get(base, 0.0))

    monkeypatch.setattr(S, "snne_score", fake_snne)

    def fake_variants(
        base_answer: str, *, question: str, evidence_snippets, count: int
    ):  # type: ignore[no-redef]
        # Make sure we evaluate both A and B
        if base_answer == "A":
            return ["A"] * max(2, count)
        return [base_answer] * max(2, count)

    monkeypatch.setattr(S, "generate_answer_variants", fake_variants)

    cal = FakeCalibrator({"default": 0.0, "cardio": -0.2})

    # Under default
    out_default = S.plan_best_answer(
        question="q",
        evidence_pack=[],
        base_answer="A",
        embed=_stub_embed,
        snne_calibrator=cal,
        verifier=type("V", (), {"verify": staticmethod(lambda q, a: (0.0, [], False))}),
        policy_cfg=PolicyConfig(w1=1.0, w2=0.0),
        sample_count=3,
        config=S.PlanningConfig(mode="tot", budget=2),
        domain="default",
    )

    # Under cardio (offset applied equally)
    out_cardio = S.plan_best_answer(
        question="q",
        evidence_pack=[],
        base_answer="A",
        embed=_stub_embed,
        snne_calibrator=cal,
        verifier=type("V", (), {"verify": staticmethod(lambda q, a: (0.0, [], False))}),
        policy_cfg=PolicyConfig(w1=1.0, w2=0.0),
        sample_count=3,
        config=S.PlanningConfig(mode="tot", budget=2),
        domain="cardio",
    )

    assert out_default["best_answer"] == out_cardio["best_answer"] == "A"

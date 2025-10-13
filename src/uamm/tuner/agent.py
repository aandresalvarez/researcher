from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional

from uamm.config.settings import Settings


@dataclass
class TunerTargets:
    """Guardrail thresholds used by the tuner when evaluating metrics."""

    false_accept_max: float = 0.05
    accept_min: float = 0.6
    latency_p95_max: float = 6.0
    abstain_max: float = 0.25

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "TunerTargets":
        if not payload:
            return cls()
        filtered: Dict[str, Any] = {}
        for field in (
            "false_accept_max",
            "accept_min",
            "latency_p95_max",
            "abstain_max",
        ):
            if field in payload and payload[field] is not None:
                filtered[field] = float(payload[field])
        return cls(**filtered)


@dataclass
class TunerProposal:
    """A structured proposal from the Tuner agent."""

    config_patch: Dict[str, Any]
    notes: List[str]
    issues: List[str]
    analysis: Dict[str, Any]
    requires_approval: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_patch": self.config_patch,
            "notes": list(self.notes),
            "issues": list(self.issues),
            "analysis": dict(self.analysis),
            "requires_approval": self.requires_approval,
        }


class TunerAgent:
    """Simple heuristics-based tuner that proposes safer configuration values."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def propose(
        self,
        *,
        suite_results: Iterable[Dict[str, Any]],
        targets: TunerTargets,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> TunerProposal:
        suite_results = list(suite_results)
        metrics = metrics or {}

        current_accept = float(self._settings.accept_threshold)
        current_delta = float(getattr(self._settings, "borderline_delta", 0.05))
        current_snne_samples = int(getattr(self._settings, "snne_samples", 5))
        current_max_refine = int(getattr(self._settings, "max_refinement_steps", 2))

        false_accept_rates: List[float] = []
        accept_rates: List[float] = []
        abstain_rates: List[float] = []
        latency_p95: List[float] = []

        suite_metrics_summary: List[Dict[str, Any]] = []
        for result in suite_results:
            suite_metrics = dict(result.get("metrics", {}))
            suite_id = result.get("suite_id")
            suite_summary = {
                "suite_id": suite_id,
                "metrics": suite_metrics,
            }
            suite_metrics_summary.append(suite_summary)
            if (
                "false_accept_rate" in suite_metrics
                and suite_metrics["false_accept_rate"] is not None
            ):
                false_accept_rates.append(float(suite_metrics["false_accept_rate"]))
            if (
                "accept_rate" in suite_metrics
                and suite_metrics["accept_rate"] is not None
            ):
                accept_rates.append(float(suite_metrics["accept_rate"]))
            if (
                "abstain_rate" in suite_metrics
                and suite_metrics["abstain_rate"] is not None
            ):
                abstain_rates.append(float(suite_metrics["abstain_rate"]))
            if (
                "latency_p95" in suite_metrics
                and suite_metrics["latency_p95"] is not None
            ):
                latency_p95.append(float(suite_metrics["latency_p95"]))

        # Fold in global metrics when provided
        if "false_accept_rate" in metrics and metrics["false_accept_rate"] is not None:
            false_accept_rates.append(float(metrics["false_accept_rate"]))
        if "accept_rate" in metrics and metrics["accept_rate"] is not None:
            accept_rates.append(float(metrics["accept_rate"]))
        if "abstain_rate" in metrics and metrics["abstain_rate"] is not None:
            abstain_rates.append(float(metrics["abstain_rate"]))
        if "latency_p95" in metrics and metrics["latency_p95"] is not None:
            latency_p95.append(float(metrics["latency_p95"]))

        max_false_accept = max(false_accept_rates) if false_accept_rates else None
        min_accept_rate = min(accept_rates) if accept_rates else None
        max_abstain_rate = max(abstain_rates) if abstain_rates else None
        max_latency = max(latency_p95) if latency_p95 else None

        config_patch: Dict[str, Any] = {}
        notes: List[str] = []
        issues: List[str] = []

        if max_false_accept is not None and max_false_accept > targets.false_accept_max:
            new_tau = min(round(current_accept + 0.02, 4), 0.99)
            if new_tau > current_accept:
                config_patch["accept_threshold"] = new_tau
                notes.append(
                    f"Increase accept_threshold to {new_tau:.3f} to lower false-accept ({max_false_accept:.3f})."
                )
            issues.append(
                f"false_accept_rate {max_false_accept:.3f} exceeds target {targets.false_accept_max:.3f}"
            )

        if min_accept_rate is not None and min_accept_rate < targets.accept_min:
            if "accept_threshold" not in config_patch:
                new_tau = max(round(current_accept - 0.02, 4), 0.5)
                if new_tau < current_accept:
                    config_patch["accept_threshold"] = new_tau
                    notes.append(
                        f"Lower accept_threshold to {new_tau:.3f} to lift acceptance ({min_accept_rate:.3f})."
                    )
            issues.append(
                f"accept_rate {min_accept_rate:.3f} below target {targets.accept_min:.3f}"
            )

        if max_abstain_rate is not None and max_abstain_rate > targets.abstain_max:
            new_delta = max(round(current_delta - 0.01, 4), 0.01)
            if new_delta < current_delta:
                config_patch.setdefault("borderline_delta", new_delta)
                notes.append(
                    f"Reduce borderline_delta to {new_delta:.3f} to shrink abstentions ({max_abstain_rate:.3f})."
                )
            if "accept_threshold" not in config_patch:
                new_tau = max(round(current_accept - 0.01, 4), 0.5)
                if new_tau < current_accept:
                    config_patch["accept_threshold"] = new_tau
                    notes.append(
                        "Relax accept_threshold slightly to curb abstain rate."
                    )
            issues.append(
                f"abstain_rate {max_abstain_rate:.3f} above target {targets.abstain_max:.3f}"
            )

        if max_latency is not None and max_latency > targets.latency_p95_max:
            if current_snne_samples > 3:
                config_patch["snne_samples"] = max(3, current_snne_samples - 1)
                notes.append("Reduce SNNE samples to trim latency.")
            if current_max_refine > 1:
                config_patch["max_refinement_steps"] = 1
                notes.append("Limit refinements to 1 to hold latency budget.")
            issues.append(
                f"latency_p95 {max_latency:.3f}s exceeds target {targets.latency_p95_max:.3f}s"
            )

        if not config_patch:
            notes.append(
                "Current configuration satisfies tuner targets; no change proposed."
            )

        analysis = {
            "targets": asdict(targets),
            "max_false_accept_rate": max_false_accept,
            "min_accept_rate": min_accept_rate,
            "max_abstain_rate": max_abstain_rate,
            "max_latency_p95": max_latency,
            "suite_metrics": suite_metrics_summary,
            "input_metrics": metrics,
        }

        return TunerProposal(
            config_patch=config_patch,
            notes=notes,
            issues=issues,
            analysis=analysis,
        )

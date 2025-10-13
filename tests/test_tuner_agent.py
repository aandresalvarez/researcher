from uamm.config.settings import Settings
from uamm.tuner.agent import TunerAgent, TunerTargets


def test_tuner_agent_proposes_adjustments():
    settings = Settings()
    agent = TunerAgent(settings)
    suite_results = [
        {
            "suite_id": "CP-B1",
            "metrics": {
                "false_accept_rate": 0.2,
                "accept_rate": 0.4,
                "abstain_rate": 0.2,
            },
        }
    ]
    metrics = {"latency_p95": 7.0, "abstain_rate": 0.4}
    proposal = agent.propose(
        suite_results=suite_results, targets=TunerTargets(), metrics=metrics
    )
    patch = proposal.config_patch
    assert "accept_threshold" in patch or "borderline_delta" in patch
    assert proposal.requires_approval is True

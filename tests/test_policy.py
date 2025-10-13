from uamm.policy.policy import PolicyConfig, final_score, decide


def test_policy_decide_accept():
    cfg = PolicyConfig(tau_accept=0.5, delta=0.1)
    S = final_score(snne_norm=0.1, s2=0.9, cfg=cfg)
    assert decide(S, cfg, cp_accept=True) == "accept"


def test_policy_decide_iterate():
    cfg = PolicyConfig(tau_accept=0.8, delta=0.1)
    # S slightly below threshold within delta
    S = 0.75
    assert decide(S, cfg, cp_accept=True) == "iterate"


def test_policy_decide_abstain():
    cfg = PolicyConfig(tau_accept=0.8, delta=0.05)
    S = 0.6
    assert decide(S, cfg, cp_accept=True) == "abstain"
